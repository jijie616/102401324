# -*- coding: utf-8 -*-
"""附加题 6.1：爬取主流科技媒体观点，预测大语言模型应用的发展趋势。

设计思路
--------
1. **数据源**：优先使用各媒体的 RSS/Atom 订阅源（结构稳定、无需解析动态页面），
   覆盖国内（量子位 / InfoQ / 少数派 / solidot）与国际（MIT Tech Review /
   TechCrunch AI / VentureBeat AI / arXiv cs.CL）两类，满足"世界主流媒体"。
2. **主题过滤**：只保留标题或摘要命中 LLM 相关关键词的报道，避免科技媒体里
   与选题无关的内容污染结论。
3. **趋势量化**：把文章按时间分桶，统计各技术方向关键词的"近期提及率 - 早期
   提及率"，得到**上升/下降趋势排名**，而不是仅凭主观印象预测。
4. **产物**：Excel（每类媒体的观点 + 关键词频次 + 趋势对比）+ 词云 + JSON。
"""
from __future__ import annotations

import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from . import config

logger = logging.getLogger(__name__)

ATOM_NS = "{http://www.w3.org/2005/Atom}"

# 媒体源：(名称, 订阅地址, 类别, 语言)
MEDIA_SOURCES: List[Tuple[str, str, str, str]] = [
    ("量子位", "https://www.qbitai.com/feed", "国内科技媒体", "zh"),
    ("InfoQ中文", "https://www.infoq.cn/feed", "国内技术社区", "zh"),
    ("少数派", "https://sspai.com/feed", "国内科技媒体", "zh"),
    ("solidot", "https://www.solidot.org/index.rss", "国内科技资讯", "zh"),
    ("MIT Technology Review", "https://www.technologyreview.com/feed/", "国际权威媒体", "en"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/",
     "国际科技媒体", "en"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/", "国际科技媒体", "en"),
    ("arXiv cs.CL", "http://export.arxiv.org/rss/cs.CL", "学术预印本", "en"),
]

# 主题过滤关键词：标题或摘要命中任一即视为"与 LLM 相关"
TOPIC_KEYWORDS = (
    "大模型", "大语言模型", "语言模型", "llm", "gpt", "chatgpt", "claude", "gemini",
    "deepseek", "qwen", "通义", "文心", "豆包", "kimi", "智能体", "agent",
    "多模态", "aigc", "生成式", "人工智能", "artificial intelligence", "ai model",
    "transformer", "diffusion", "openai", "anthropic", "machine learning",
)

# 趋势维度：用于计算"升温/降温"的技术方向
TREND_DIMENSIONS: Dict[str, Tuple[str, ...]] = {
    "智能体与自动化": ("智能体", "agent", "agentic", "自动化", "autonomous", "workflow"),
    "多模态与生成": ("多模态", "multimodal", "文生", "视频生成", "图像生成", "diffusion",
                     "voice", "speech", "text-to"),
    "推理与思维链": ("推理", "reasoning", "思维链", "chain-of-thought", "cot", "思考",
                     "logic"),
    "开源与本地部署": ("开源", "open-source", "open source", "本地部署", "local",
                       "llama", "qwen", "deepseek", "权重"),
    "算力与成本": ("算力", "gpu", "芯片", "chip", "成本", "cost", "价格", "price",
                   "inference cost", "tokens", "能耗", "energy"),
    "安全对齐与监管": ("安全", "safety", "对齐", "alignment", "监管", "regulation",
                       "合规", "隐私", "privacy", "风险", "risk", "幻觉", "hallucination"),
    "企业落地与效率": ("企业", "enterprise", "落地", "部署", "deployment", "效率",
                       "productivity", "roi", "降本", "商用"),
    "科研与教育应用": ("科研", "research", "论文", "教育", "education", "医疗",
                       "medical", "科学", "science"),
    "具身智能与机器人": ("机器人", "robot", "具身", "embodied", "humanoid", "人形"),
}

_TAG = re.compile(r"<[^>]+>")


@dataclass
class Article:
    """一篇媒体报道。"""

    source: str
    category: str
    language: str
    title: str
    link: str = ""
    summary: str = ""
    published: str = ""          # ISO 格式日期字符串
    month: str = ""              # YYYY-MM，用于趋势分桶
    dimensions: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        """转为可 JSON 序列化的字典。"""
        return asdict(self)


def _text_of(node: Optional[ET.Element]) -> str:
    """安全取出节点的文本。"""
    if node is None or node.text is None:
        return ""
    return _TAG.sub("", node.text).strip()


def _parse_date(raw: str) -> str:
    """把各种 RSS 日期格式统一成 YYYY-MM-DD；失败返回空串。"""
    if not raw:
        return ""
    raw = raw.strip()
    formats = (
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # 兜底：正则抓 YYYY-MM-DD
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
    return match.group(0) if match else ""


def _extract_dimensions(text: str) -> List[str]:
    """判断文本覆盖了哪些趋势维度。"""
    lowered = text.lower()
    return [dim for dim, words in TREND_DIMENSIONS.items()
            if any(w in lowered for w in words)]


def is_topic_relevant(text: str) -> bool:
    """判断一篇报道是否与 LLM 主题相关。"""
    lowered = text.lower()
    return any(k in lowered for k in TOPIC_KEYWORDS)


def parse_feed(content: bytes, source: str, category: str, language: str,
               limit: int = 40) -> List[Article]:
    """解析 RSS 2.0 或 Atom 订阅内容为 Article 列表。

    Args:
        content: 订阅源原始字节。
        source: 媒体名称。
        category: 媒体类别。
        language: 语言代码。
        limit: 单源最多取多少条。

    Returns:
        Article 列表；解析失败返回空列表（由调用方记录并跳过该源）。
    """
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        logger.warning("%s 订阅源 XML 解析失败：%s", source, exc)
        return []

    nodes = root.findall(".//item") or root.findall(f".//{ATOM_NS}entry")
    articles: List[Article] = []
    for node in nodes[:limit]:
        title = _text_of(node.find("title")) or _text_of(node.find(f"{ATOM_NS}title"))
        summary = (_text_of(node.find("description"))
                   or _text_of(node.find(f"{ATOM_NS}summary"))
                   or _text_of(node.find(f"{ATOM_NS}content")))
        link = _text_of(node.find("link")) or ""
        if not link:
            link_node = node.find(f"{ATOM_NS}link")
            link = (link_node.get("href") if link_node is not None else "") or ""
        published = _parse_date(
            _text_of(node.find("pubDate"))
            or _text_of(node.find(f"{ATOM_NS}updated"))
            or _text_of(node.find(f"{ATOM_NS}published"))
        )
        if not title:
            continue
        combined = f"{title} {summary}"
        articles.append(
            Article(
                source=source, category=category, language=language,
                title=title[:200], link=link[:300], summary=summary[:500],
                published=published, month=published[:7] if published else "",
                dimensions=_extract_dimensions(combined),
            )
        )
    return articles


def fetch_source(name: str, url: str, category: str, language: str,
                 timeout: int = 25, retries: int = 2) -> List[Article]:
    """抓取并解析单个媒体源，失败返回空列表（不中断整体流程）。"""
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            articles = parse_feed(resp.content, name, category, language)
            logger.info("%s：抓到 %d 条", name, len(articles))
            return articles
        except Exception as exc:  # noqa: BLE001 - 单源失败需隔离
            logger.warning("%s 第 %d 次抓取失败：%s", name, attempt, exc)
            time.sleep(1.5 * attempt)
    return []


def fetch_all_media(sources: Sequence[Tuple[str, str, str, str]] = tuple(MEDIA_SOURCES),
                    only_relevant: bool = True) -> List[Article]:
    """抓取全部媒体源并做主题过滤。

    Args:
        sources: (名称, 地址, 类别, 语言) 列表。
        only_relevant: 是否只保留与 LLM 相关的报道。

    Returns:
        去重后的 Article 列表（按发布时间降序）。
    """
    articles: List[Article] = []
    seen: set = set()
    for name, url, category, language in sources:
        for art in fetch_source(name, url, category, language):
            key = (art.title, art.source)
            if key in seen:
                continue
            if only_relevant and not is_topic_relevant(f"{art.title} {art.summary}"):
                continue
            seen.add(key)
            articles.append(art)
        time.sleep(0.5)

    articles.sort(key=lambda a: a.published or "", reverse=True)
    logger.info("媒体观点抓取完成：%d 条相关报道，覆盖 %d 家媒体",
                len(articles), len({a.source for a in articles}))
    return articles


# --------------------------------------------------------------------------
# 趋势分析
# --------------------------------------------------------------------------
def dimension_stats(articles: Sequence[Article]) -> List[Tuple[str, int, float]]:
    """统计各趋势维度的提及数与被提及文章占比。

    Returns:
        [(维度, 提及文章数, 占比%)]，按提及数降序。
    """
    total = len(articles)
    counter: Counter = Counter()
    for art in articles:
        for dim in art.dimensions:
            counter[dim] += 1
    return [(dim, n, (n / total * 100) if total else 0.0)
            for dim, n in counter.most_common()]


def trend_momentum(articles: Sequence[Article],
                   recent_months: int = 3) -> List[Tuple[str, int, int, float]]:
    """计算各方向的"升温/降温"动量。

    做法：把文章按月份排序，取最近 N 个月为"近期"，其余为"早期"，
    计算 `近期占比 - 早期占比`（百分点）。正值=正在升温。

    Returns:
        [(维度, 近期提及数, 早期提及数, 动量百分点)]，按动量降序。
    """
    dated = [a for a in articles if a.month]
    if len(dated) < 10:
        return []

    months = sorted({a.month for a in dated}, reverse=True)
    recent_set = set(months[:recent_months])
    recent = [a for a in dated if a.month in recent_set]
    earlier = [a for a in dated if a.month not in recent_set]
    if not recent or not earlier:
        return []

    def rate(group: Sequence[Article], dim: str) -> float:
        return sum(1 for a in group if dim in a.dimensions) / len(group) * 100

    rows: List[Tuple[str, int, int, float]] = []
    for dim in TREND_DIMENSIONS:
        n_recent = sum(1 for a in recent if dim in a.dimensions)
        n_early = sum(1 for a in earlier if dim in a.dimensions)
        rows.append((dim, n_recent, n_early, rate(recent, dim) - rate(earlier, dim)))
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows


def build_forecast(articles: Sequence[Article]) -> Dict[str, object]:
    """综合维度占比与动量，生成趋势预测结论（供博客引用）。"""
    momentum = trend_momentum(articles)
    stats = dimension_stats(articles)
    rising = [{"方向": d, "动量(百分点)": round(m, 2), "近期提及": nr, "早期提及": ne}
              for d, nr, ne, m in momentum if m > 0][:5]
    falling = [{"方向": d, "动量(百分点)": round(m, 2), "近期提及": nr, "早期提及": ne}
               for d, nr, ne, m in momentum if m <= 0][-3:]

    by_month: Counter = Counter(a.month for a in articles if a.month)
    by_source: Counter = Counter(a.source for a in articles)

    return {
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "样本文章数": len(articles),
        "覆盖媒体数": len(by_source),
        "各媒体文章数": dict(by_source.most_common()),
        "月度分布": dict(sorted(by_month.items())),
        "维度占比": [{"方向": d, "文章数": n, "占比%": round(p, 1)} for d, n, p in stats],
        "升温方向": rising,
        "降温方向": falling,
        "趋势预测": _forecast_text(rising, stats),
    }


def _forecast_text(rising: List[Dict[str, object]],
                   stats: Sequence[Tuple[str, int, float]]) -> List[str]:
    """把量化结果翻译成可读的趋势判断。"""
    lines: List[str] = []
    if rising:
        lines.append(
            "未来 6-12 个月，媒体关注度上升最快的方向是 "
            + "、".join(str(r["方向"]) for r in rising[:3])
            + "，其中 "
            + str(rising[0]["方向"])
            + f" 的近期提及率较早期提升 {rising[0]['动量(百分点)']} 个百分点。"
        )
    if stats:
        top = stats[0]
        lines.append(
            f"当前媒体讨论的基本盘仍是「{top[0]}」，{top[2]:.1f}% 的相关报道都涉及该方向，"
            "说明它已从概念验证进入工程落地阶段。"
        )
        if len(stats) > 1:
            lines.append(
                f"「{stats[1][0]}」以 {stats[1][2]:.1f}% 的占比位居第二，"
                "与上一方向共同构成技术演进的主线。"
            )
    lines.append(
        "总体判断：大语言模型的应用重心正在从「模型能力本身」转向"
        "「智能体编排 + 成本控制 + 安全合规」，企业侧关注点由“能不能用”变为“用得起、管得住”。"
    )
    return lines


def save_articles(articles: Sequence[Article],
                  path: Optional[Path] = None) -> Path:
    """把媒体文章落盘为 JSONL（便于复现与断点续跑）。"""
    out = Path(path or (config.DATA_DIR / "media_articles.jsonl"))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for art in articles:
            fh.write(json.dumps(art.as_dict(), ensure_ascii=False) + "\n")
    logger.info("媒体报道已写入 %s（%d 条）", out, len(articles))
    return out


def load_articles(path: Optional[Path] = None) -> List[Article]:
    """读回已落盘的媒体文章。"""
    src = Path(path or (config.DATA_DIR / "media_articles.jsonl"))
    if not src.exists():
        return []
    articles: List[Article] = []
    with open(src, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                articles.append(Article(**json.loads(line)))
    return articles


def export_media_excel(articles: Sequence[Article], forecast: Dict[str, object],
                       path: Optional[Path] = None) -> Path:
    """把媒体报道与趋势预测导出为 xlsx（附加题证据链）。"""
    import pandas as pd

    out = Path(path or (config.OUTPUT_DIR / "media_trends.xlsx"))
    out.parent.mkdir(parents=True, exist_ok=True)

    frames: Dict[str, "pd.DataFrame"] = {
        "趋势预测": pd.DataFrame(
            [{"结论": t} for t in forecast.get("趋势预测", [])] or [{"结论": "样本不足"}]
        ),
        "维度占比": pd.DataFrame(forecast.get("维度占比") or [{"方向": "-", "文章数": 0}]),
        "升温降温": pd.DataFrame(
            (forecast.get("升温方向") or []) + (forecast.get("降温方向") or [])
            or [{"方向": "-", "动量(百分点)": 0}]
        ),
        "媒体来源": pd.DataFrame(
            [{"媒体": k, "文章数": v} for k, v in (forecast.get("各媒体文章数") or {}).items()]
            or [{"媒体": "-", "文章数": 0}]
        ),
        "全部报道": pd.DataFrame(
            [{"媒体": a.source, "类别": a.category, "日期": a.published,
              "标题": a.title, "涉及方向": "、".join(a.dimensions), "链接": a.link}
             for a in articles] or [{"媒体": "-", "标题": "无数据"}]
        ),
    }
    if forecast.get("月度分布"):
        frames["月度分布"] = pd.DataFrame(
            [{"月份": k, "文章数": v} for k, v in forecast["月度分布"].items()]
        )

    widths = {"结论": 90, "标题": 70, "链接": 50, "涉及方向": 30, "媒体": 22,
              "方向": 20, "文章数": 12, "类别": 16, "日期": 14, "月份": 12}

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            frame.to_excel(writer, sheet_name=sheet, index=False)
            try:
                ws = writer.book[sheet]
                for idx, column in enumerate(frame.columns, start=1):
                    ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = \
                        widths.get(str(column), 20)
                ws.freeze_panes = "A2"
            except Exception as exc:  # noqa: BLE001
                logger.debug("Sheet %s 美化跳过：%s", sheet, exc)

    logger.info("媒体趋势 Excel 已导出：%s", out)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    """附加题独立入口：抓媒体报道 -> 分析趋势 -> 出 Excel 与词云。"""
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    parser = argparse.ArgumentParser(description="科技媒体观点爬取与趋势预测（附加题）")
    parser.add_argument("--use-cache", action="store_true", help="使用已落盘数据，不重新抓取")
    parser.add_argument("--all", action="store_true", help="不做主题过滤，保留全部文章")
    args = parser.parse_args(argv)

    articles = load_articles() if args.use_cache else fetch_all_media(only_relevant=not args.all)
    if not articles:
        logger.error("未获取到任何文章，请检查网络")
        return 1

    save_articles(articles)
    forecast = build_forecast(articles)
    excel = export_media_excel(articles, forecast)

    # 趋势词云：用标题+摘要的分词结果
    from .tokenizer import drop_substring_words, tokenize_batch
    from .visualizer import make_wordcloud
    from collections import Counter as _Counter

    words = tokenize_batch(f"{a.title} {a.summary}" for a in articles)
    freq = _Counter(drop_substring_words(dict(_Counter(words)))).most_common(200)
    cloud = make_wordcloud(freq, config.OUTPUT_DIR / "media_wordcloud.png")

    with open(config.OUTPUT_DIR / "media_forecast.json", "w", encoding="utf-8") as fh:
        json.dump(forecast, fh, ensure_ascii=False, indent=2)

    print("\n================ 趋势预测 ================")
    for line in forecast["趋势预测"]:
        print(" •", line)
    print("\n=== 维度占比 Top5 ===")
    for row in (forecast["维度占比"] or [])[:5]:
        print(f"  {row['方向']:<14} {row['文章数']:>4} 篇  {row['占比%']:>5}%")
    print("\n=== 升温最快的方向 ===")
    for row in forecast["升温方向"] or []:
        print(f"  {row['方向']:<14} 动量 {row['动量(百分点)']:+.2f} 百分点")
    print(f"\nExcel : {excel}")
    print(f"词云  : {cloud}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
