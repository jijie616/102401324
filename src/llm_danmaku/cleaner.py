# -*- coding: utf-8 -*-
"""弹幕清洗：把原始弹幕列表变成可用于统计的干净列表。

处理链：标准化 -> 去 @/URL -> 噪声过滤 -> 去重 -> 长度约束
每一步都可单独调用，方便单元测试。
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Tuple

from . import config
from .filters import is_noise, normalize, strip_mentions

logger = logging.getLogger(__name__)


@dataclass
class CleanReport:
    """清洗过程的数据质量报告，可直接写入 Excel 与博客。"""

    total: int = 0
    kept: int = 0
    dropped_noise: int = 0
    dropped_duplicate: int = 0
    dropped_empty: int = 0
    duplicate_top: List[Tuple[str, int]] = field(default_factory=list)

    @property
    def noise_ratio(self) -> float:
        """噪声占比（含重复刷屏）。"""
        if self.total == 0:
            return 0.0
        return (self.dropped_noise + self.dropped_empty) / self.total

    @property
    def keep_ratio(self) -> float:
        """有效弹幕占比。"""
        return self.kept / self.total if self.total else 0.0

    def as_dict(self) -> Dict[str, object]:
        """转为可序列化字典。"""
        return {
            "原始弹幕数": self.total,
            "清洗后弹幕数": self.kept,
            "噪声过滤数": self.dropped_noise,
            "重复刷屏数": self.dropped_duplicate,
            "空内容数": self.dropped_empty,
            "保留率": round(self.keep_ratio, 4),
            "噪声率": round(self.noise_ratio, 4),
        }


def clean_text(text: str) -> str:
    """对单条弹幕做标准化与去噪处理。

    Returns:
        清洗后的文本；若为空字符串表示应丢弃。
    """
    s = normalize(text)
    if not s:
        return ""
    s = normalize(strip_mentions(s))
    if is_noise(s, config.MIN_DANMAKU_LEN, config.MAX_DANMAKU_LEN):
        return ""
    return s


def clean_batch(
    texts: Iterable[str],
    deduplicate: bool = True,
) -> Tuple[List[str], CleanReport]:
    """批量清洗。

    Args:
        texts: 原始弹幕文本序列（可含重复）。
        deduplicate: 是否去掉重复弹幕（复读机式刷屏只保留一次）。

    Returns:
        (清洗后的弹幕列表, 清洗报告)
    """
    report = CleanReport()
    seen: set = set()
    kept: List[str] = []
    duplicates: Counter = Counter()

    for raw in texts:
        report.total += 1
        cleaned = clean_text(raw)
        if not cleaned:
            if not normalize(raw):
                report.dropped_empty += 1
            else:
                report.dropped_noise += 1
            continue
        if deduplicate:
            if cleaned in seen:
                report.dropped_duplicate += 1
                duplicates[cleaned] += 1
                continue
            seen.add(cleaned)
        kept.append(cleaned)

    report.kept = len(kept)
    report.duplicate_top = duplicates.most_common(10)
    logger.info(
        "清洗完成：原始 %d 条 -> 保留 %d 条（噪声 %d，重复 %d），保留率 %.1f%%",
        report.total, report.kept, report.dropped_noise,
        report.dropped_duplicate, report.keep_ratio * 100,
    )
    return kept, report
