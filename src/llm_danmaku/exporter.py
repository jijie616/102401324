# -*- coding: utf-8 -*-
"""Excel 导出：把统计结果写入多 Sheet 的 xlsx（作业 2.2 要求）。

Sheet 设计：
    数据总览     关键指标 + 数据质量（噪声率、保留率）
    Top弹幕      数量排名前 8 的弹幕及其词频
    LLM相关弹幕  与"大模型/LLM/GPT"等强相关的 Top 8
    词频统计     Top-N 高频词
    视频清单     综合排序前 300 的视频及其弹幕量
    领域分布     应用领域占比
    情感与立场   正/负/中性、成本、风险、收益
    时间分布     弹幕在视频时间轴上的分布
    重复刷屏     清洗前的高频复读弹幕
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from . import config
from .analyzer import DanmakuStats

logger = logging.getLogger(__name__)

# 中文字体与列宽（让作业里的表格直接好看，不用手工调）
_HEADER_FILL = "FF1F4E79"
_HEADER_FONT = "FFFFFFFF"


def _write_sheet(writer: pd.DataFrame, frame: pd.DataFrame, sheet: str,
                 column_widths: Optional[Dict[str, int]] = None) -> None:
    """写一个 Sheet 并统一美化表头与列宽。"""
    frame.to_excel(writer, sheet_name=sheet, index=False)
    try:
        book = writer.book
        if sheet not in book.sheetnames:
            return
        ws = book[sheet]
        for cell in ws[1]:
            cell.fill = _fill()
            cell.font = _font()
            cell.alignment = _align()
        for idx, column in enumerate(frame.columns, start=1):
            letter = ws.cell(row=1, column=idx).column_letter
            width = (column_widths or {}).get(str(column), 18)
            ws.column_dimensions[letter].width = width
        ws.freeze_panes = "A2"
    except Exception as exc:  # noqa: BLE001 - 美化失败不应影响数据写入
        logger.debug("Sheet %s 样式设置跳过：%s", sheet, exc)


def _fill():
    from openpyxl.styles import PatternFill
    return PatternFill("solid", fgColor=_HEADER_FILL)


def _font():
    from openpyxl.styles import Font
    return Font(color=_HEADER_FONT, bold=True)


def _align():
    from openpyxl.styles import Alignment
    return Alignment(horizontal="center", vertical="center")


def export_to_excel(stats: DanmakuStats, path: Path = config.EXCEL_FILE) -> Path:
    """把统计结果导出为 xlsx。

    Args:
        stats: analyze() 的返回值。
        path: 输出路径。

    Returns:
        实际写入的文件路径。
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    overview: List[Tuple[str, object]] = [
        ("生成时间", stats.generated_at),
        ("抓取视频数", stats.video_count),
        ("抓取失败视频数", stats.failed_videos),
        ("原始弹幕总数", stats.total_raw),
        ("清洗后弹幕总数", stats.total_cleaned),
        ("去重后唯一弹幕数", stats.total_unique),
        ("覆盖词汇量", len(stats.top_words)),
        ("涉及应用领域数", len(stats.domain_counts)),
    ]
    for key, value in (stats.clean_report or {}).items():
        overview.append((key, value))
    overview.extend([
        ("正面弹幕数", stats.positive_count),
        ("负面弹幕数", stats.negative_count),
        ("中性弹幕数", stats.neutral_count),
        ("提及成本/价格", stats.cost_count),
        ("提及风险/隐患", stats.risk_count),
        ("提及效率/收益", stats.benefit_count),
    ])

    frames: Dict[str, pd.DataFrame] = {
        "数据总览": pd.DataFrame(overview, columns=["指标", "数值"]),
        "Top弹幕": pd.DataFrame(stats.top_comments, columns=["弹幕内容", "出现次数"]),
        "LLM相关弹幕": pd.DataFrame(stats.top_llm_comments, columns=["弹幕内容", "出现次数"]),
        "词频统计": pd.DataFrame(stats.top_words, columns=["词语", "词频"]),
        "视频清单": pd.DataFrame(
            [
                {
                    "排名": v.rank, "BV号": v.bvid, "标题": v.title,
                    "UP主": v.author, "播放量": v.play,
                    "抓取弹幕数": v.raw_count, "去重后弹幕数": v.unique_count,
                    "该视频Top弹幕": " / ".join(f"{t}({c})" for t, c in v.top_comments[:3]),
                }
                for v in stats.video_stats
            ]
        ),
        "领域分布": pd.DataFrame(
            sorted(stats.domain_counts.items(), key=lambda kv: kv[1], reverse=True),
            columns=["应用领域", "弹幕提及数"],
        ),
        "情感与立场": pd.DataFrame(
            [
                {"类别": "正面", "数量": stats.positive_count},
                {"类别": "负面", "数量": stats.negative_count},
                {"类别": "中性", "数量": stats.neutral_count},
                {"类别": "成本相关", "数量": stats.cost_count},
                {"类别": "风险相关", "数量": stats.risk_count},
                {"类别": "收益相关", "数量": stats.benefit_count},
            ]
        ),
        "时间分布": pd.DataFrame(stats.progress_buckets, columns=["视频进度区间", "弹幕数"]),
        "重复刷屏": pd.DataFrame(stats.repeated_comments, columns=["弹幕内容", "出现次数"]),
    }

    widths = {
        "指标": 20, "数值": 14, "弹幕内容": 52, "出现次数": 12,
        "词语": 18, "词频": 10, "标题": 46, "该视频Top弹幕": 60,
        "应用领域": 16, "弹幕提及数": 14, "类别": 16, "数量": 12,
        "视频进度区间": 16, "弹幕数": 12, "BV号": 16, "UP主": 18,
    }

    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for sheet, frame in frames.items():
            if frame.empty:
                frame = pd.DataFrame({"提示": ["暂无数据"]})
            _write_sheet(writer, frame, sheet, widths)

    logger.info("Excel 已导出：%s（%d 个 Sheet）", out, len(frames))
    return out


def export_videos_json(videos: Sequence[object], path: Path = config.VIDEO_LIST_FILE) -> Path:
    """把视频列表写入 JSON（爬虫中间产物，便于复现）。"""
    import json

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = [v.as_dict() if hasattr(v, "as_dict") else v for v in videos]
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    logger.info("视频列表已写入 %s（%d 条）", out, len(payload))
    return out
