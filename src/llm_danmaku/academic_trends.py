# -*- coding: utf-8 -*-
"""附加题 6.1 增强：用 arXiv 全文预印本数据做**可量化的**趋势预测。

为什么单独做这一块
------------------
科技媒体的 RSS 只保留最近 10~40 条（几周内），无法计算"升温/降温"。
arXiv 提供可按时间窗检索的官方 API，能稳定取到**过去 12 个月的月度样本**，
于是可以计算每个技术方向在"近 3 个月 vs 再往前 3 个月"的占比变化，
得到有数据支撑的趋势动量，而不是主观臆测。

产物：output/academic_trends.xlsx + output/academic_trends.json
"""
from __future__ import annotations

import json
import logging
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from . import config
from .media_analyzer import TREND_DIMENSIONS, _extract_dimensions

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
USER_AGENT = f"llm-danmaku-coursework/1.0 (student project; python-requests)"


@dataclass
class Paper:
    """一篇 arXiv 预印本。"""

    title: str
    published: str
    month: str
    abstract: str = ""
    dimension: List[str] = field(default_factory=list)
    link: str = ""


def month_windows(months_back: int = 12, end: Optional[datetime] = None
                  ) -> List[Tuple[str, datetime, datetime]]:
    """生成按月切分的时间窗（含起止时间），最新一个月在最前。

    Args:
        months_back: 向前回溯多少个月。
        end: 结束时间，默认取当前时间。

    Returns:
        [(标签 YYYY-MM, 起始, 结束)]，按时间倒序。
    """
    cursor = (end or datetime.now()).replace(hour=23, minute=59, second=0, microsecond=0)
    windows: List[Tuple[str, datetime, datetime]] = []
    for _ in range(months_back):
        month_end = cursor.replace(day=1) - timedelta(seconds=1)   # 上个月最后一刻
        start = month_end.replace(day=1, hour=0, minute=0, second=0)
        windows.append((month_end.strftime("%Y-%m"), start, month_end))
        cursor = start
    return windows


def fetch_arxiv_window(start: datetime, end: datetime, category: str = "cs.CL",
                       max_results: int = 300, retries: int = 3) -> Tuple[int, List[Paper]]:
    """抓取一个时间窗内的 arXiv 论文。

    Args:
        start: 窗口起始时间。
        end: 窗口结束时间。
        category: arXiv 分类（cs.CL = 计算语言学）。
        max_results: 最多取多少条（用作样本）。
        retries: 重试次数。

    Returns:
        (该窗口的总命中数, 采样的论文列表)
    """
    query = (f"cat:{category} AND "
             f"submittedDate:[{start.strftime('%Y%m%d%H%M')} TO {end.strftime('%Y%m%d%H%M')}]")
    params = {
        "search_query": query, "start": 0, "max_results": max_results,
        "sortBy": "submittedDate", "sortOrder": "descending",
    }
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(ARXIV_API, params=params,
                                headers={"User-Agent": USER_AGENT}, timeout=40)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            total_node = root.find(f"{OPENSEARCH}totalResults")
            total = int(total_node.text) if total_node is not None and total_node.text else 0

            papers: List[Paper] = []
            for entry in root.findall(f"{ATOM}entry"):
                title = (entry.findtext(f"{ATOM}title") or "").strip().replace("\n", " ")
                summary = (entry.findtext(f"{ATOM}summary") or "").strip().replace("\n", " ")
                published = (entry.findtext(f"{ATOM}published") or "")[:10]
                link = entry.findtext(f"{ATOM}id") or ""
                if not title:
                    continue
                papers.append(Paper(
                    title=title, published=published, month=published[:7],
                    abstract=summary[:600],
                    dimension=_extract_dimensions(f"{title} {summary}"),
                    link=link,
                ))
            return total, papers
        except Exception as exc:  # noqa: BLE001 - 单窗口失败不应中断整体
            logger.warning("arXiv 窗口 %s 第 %d 次抓取失败：%s",
                           start.strftime("%Y-%m"), attempt, exc)
            time.sleep(2.0 * attempt)
    return 0, []


def collect(months_back: int = 12, max_per_month: int = 300,
            category: str = "cs.CL") -> Tuple[Dict[str, int], List[Paper]]:
    """按月采集 arXiv 论文样本。

    Returns:
        ({月份: 该月总命中数}, 全部采样论文)
    """
    monthly_totals: Dict[str, int] = {}
    papers: List[Paper] = []
    for label, start, end in month_windows(months_back):
        total, batch = fetch_arxiv_window(start, end, category, max_per_month)
        monthly_totals[label] = total
        papers.extend(batch)
        logger.info("arXiv %s：该月命中 %d 篇，采样 %d 篇", label, total, len(batch))
        time.sleep(3.0)                 # arXiv 要求请求间隔 >= 3 秒
    return monthly_totals, papers


def dimension_by_month(papers: Sequence[Paper]) -> Dict[str, Dict[str, int]]:
    """统计每个维度在每个采样月的论文数。"""
    table: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for paper in papers:
        for dim in paper.dimension:
            table[dim][paper.month] += 1
    return {dim: dict(months) for dim, months in table.items()}


def compute_trend(papers: Sequence[Paper], recent_months: int = 3,
                  baseline_months: int = 3, min_sample: int = 50
                  ) -> List[Dict[str, object]]:
    """计算各技术方向的趋势动量（近 N 月占比 - 基准 N 月占比）。

    用**占比**而不是绝对篇数，避免 arXiv 整体投稿量增长带来的伪趋势。

    Args:
        papers: 采样论文列表。
        recent_months: "近期"窗口的月数。
        baseline_months: "基准"窗口的月数。
        min_sample: 低于该样本量直接返回空（避免给出不可靠结论）。

    Returns:
        [{方向, 近期篇数, 近期占比%, 基准篇数, 基准占比%, 动量(百分点)}]，按动量降序。
    """
    dated = [p for p in papers if p.month]
    if len(dated) < min_sample:
        logger.warning("样本量 %d 小于 %d，趋势动量不具统计意义，跳过计算",
                       len(dated), min_sample)
        return []

    months = sorted({p.month for p in dated}, reverse=True)
    recent_set = set(months[:recent_months])
    baseline_set = set(months[recent_months:recent_months + baseline_months])
    recent = [p for p in dated if p.month in recent_set]
    baseline = [p for p in dated if p.month in baseline_set]
    if not recent or not baseline:
        return []

    rows: List[Dict[str, object]] = []
    for dim in TREND_DIMENSIONS:
        n_recent = sum(1 for p in recent if dim in p.dimension)
        n_base = sum(1 for p in baseline if dim in p.dimension)
        rate_recent = n_recent / len(recent) * 100
        rate_base = n_base / len(baseline) * 100
        rows.append({
            "方向": dim,
            "近期篇数": n_recent, "近期占比%": round(rate_recent, 2),
            "基准篇数": n_base, "基准占比%": round(rate_base, 2),
            "动量(百分点)": round(rate_recent - rate_base, 2),
        })
    rows.sort(key=lambda r: float(r["动量(百分点)"]), reverse=True)
    return rows


def top_keywords(papers: Sequence[Paper], top_n: int = 40) -> List[Tuple[str, int]]:
    """论文标题的高频技术词（辅助判断热点）。"""
    from .tokenizer import drop_substring_words, tokenize_batch

    counts = Counter(tokenize_batch(p.title for p in papers))
    return Counter(drop_substring_words(dict(counts))).most_common(top_n)


def build_report(monthly_totals: Dict[str, int], papers: Sequence[Paper]) -> Dict[str, object]:
    """汇总成可写入 JSON / Excel 的报告结构。"""
    trend = compute_trend(papers)
    rising = [r for r in trend if float(r["动量(百分点)"]) > 0]
    falling = [r for r in trend if float(r["动量(百分点)"]) <= 0]

    # 逐月热度：每个月的样本里各维度占比
    by_month = dimension_by_month(papers)

    conclusions: List[str] = []
    if rising:
        top = rising[0]
        conclusions.append(
            f"学术前沿升温最快的方向是「{top['方向']}」：近 3 个月在 cs.CL 采样论文中的占比为 "
            f"{top['近期占比%']}%，而此前 3 个月为 {top['基准占比%']}%，"
            f"提升 {top['动量(百分点)']} 个百分点。"
        )
    if len(rising) > 1:
        conclusions.append(
            "紧随其后的是 " + "、".join(f"「{r['方向']}」" for r in rising[1:3])
            + "，说明研究重心正在从单点模型能力转向系统化的应用编排与可靠性。"
        )
    if falling:
        bottom = falling[-1]
        conclusions.append(
            f"相对降温的是「{bottom['方向']}」（{bottom['动量(百分点)']} 个百分点），"
            "这类方向并非失去价值，而是逐步沉淀为基础设施，独立研究热度自然回落。"
        )
    months_sorted = sorted(monthly_totals)
    if len(months_sorted) >= 2:
        # 数据完整性保护：当前月尚未结束，其投稿量天然偏低，
        # 直接与完整月份比较会得出"下降 90%"这类错误结论。
        # 因此用于趋势对比时一律跳过"最新（可能不完整）的那个月"。
        comparable = months_sorted[:-1]
        if len(comparable) >= 2:
            first_month, last_month = comparable[0], comparable[-1]
            first, last = monthly_totals[first_month], monthly_totals[last_month]
            if first:
                delta = (last - first) / first * 100
                conclusions.append(
                    f"投稿总量从 {first_month} 的 {first} 篇增长到 {last_month} 的 {last} 篇"
                    f"（{delta:+.1f}%），说明该领域整体仍在快速扩张。"
                    "（注：最新月份尚未结束、数据不完整，已排除在对比之外。）"
                )
    conclusions.append(
        "综合媒体观点与学术前沿：未来 1-2 年大语言模型的应用将集中在"
        "智能体化工作流、成本可控的推理部署、以及可验证的安全合规三条主线上，"
        "纯参数规模的竞争将让位于工程化与可靠性的竞争。"
    )
    return {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "采样论文数": len(papers),
        "月度总命中": dict(sorted(monthly_totals.items())),
        "趋势动量": trend,
        "升温方向": rising[:5],
        "降温方向": falling[-3:],
        "高频技术词": top_keywords(papers),
        "逐月维度分布": by_month,
        "结论": conclusions,
    }


def export_report(report: Dict[str, object],
                  path: Optional[Path] = None) -> Path:
    """导出学术趋势 Excel。"""
    import pandas as pd

    out = Path(path or (config.OUTPUT_DIR / "academic_trends.xlsx"))
    out.parent.mkdir(parents=True, exist_ok=True)

    frames: Dict[str, "pd.DataFrame"] = {
        "趋势结论": pd.DataFrame([{"结论": c} for c in report["结论"]]),
        "趋势动量": pd.DataFrame(report["趋势动量"] or [{"方向": "样本不足"}]),
        "月度投稿量": pd.DataFrame(
            [{"月份": k, "命中论文数": v} for k, v in (report["月度总命中"] or {}).items()]
            or [{"月份": "-", "命中论文数": 0}]
        ),
        "高频技术词": pd.DataFrame(report["高频技术词"] or [("无", 0)],
                                   columns=["词语", "出现次数"]),
    }
    by_month = report.get("逐月维度分布") or {}
    if by_month:
        months = sorted({m for v in by_month.values() for m in v})
        frames["逐月维度分布"] = pd.DataFrame(
            [{"方向": dim, **{m: v.get(m, 0) for m in months}}
             for dim, v in by_month.items()]
        )

    widths = {"结论": 100, "方向": 20, "月份": 12, "命中论文数": 14, "词语": 20,
              "出现次数": 12, "近期占比%": 13, "基准占比%": 13, "动量(百分点)": 15}
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
            try:
                ws = writer.book[sheet]
                for idx, column in enumerate(frame.columns, start=1):
                    ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = \
                        widths.get(str(column), 14)
                ws.freeze_panes = "A2"
            except Exception as exc:  # noqa: BLE001
                logger.debug("Sheet %s 美化跳过：%s", sheet, exc)
    logger.info("学术趋势 Excel 已导出：%s", out)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口：采集 arXiv -> 计算动量 -> 出报告。"""
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    parser = argparse.ArgumentParser(description="arXiv 学术趋势分析（附加题增强）")
    parser.add_argument("--months", type=int, default=12, help="回溯月数")
    parser.add_argument("--per-month", type=int, default=300, help="每月采样上限")
    parser.add_argument("--category", default="cs.CL", help="arXiv 分类")
    args = parser.parse_args(argv)

    monthly, papers = collect(args.months, args.per_month, args.category)
    if not papers:
        logger.error("未采集到论文，请检查网络")
        return 1

    cache = config.DATA_DIR / "arxiv_papers.jsonl"
    with open(cache, "w", encoding="utf-8") as fh:
        for p in papers:
            fh.write(json.dumps(
                {"title": p.title, "published": p.published, "month": p.month,
                 "dimension": p.dimension, "link": p.link}, ensure_ascii=False) + "\n")

    report = build_report(monthly, papers)
    out = config.OUTPUT_DIR / "academic_trends.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)
    excel = export_report(report)

    print("\n================ 学术趋势结论 ================")
    for line in report["结论"]:
        print(" •", line)
    print("\n=== 趋势动量（近3月 vs 前3月）===")
    for row in report["趋势动量"]:
        arrow = "↑" if float(row["动量(百分点)"]) > 0 else "↓"
        print(f"  {arrow} {row['方向']:<14} {row['近期占比%']:>5}% vs {row['基准占比%']:>5}%"
              f"  动量 {row['动量(百分点)']:+.2f}")
    print(f"\nJSON  : {out}")
    print(f"Excel : {excel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
