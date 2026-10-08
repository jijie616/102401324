# -*- coding: utf-8 -*-
"""数据可视化：词云 + 静态图表（作业 2.3 要求"越美观越好"）。

字体策略：词云必须显式指定中文字体，否则中文会渲染成方框。
本模块统一从 config.FONT_PATH 取字体，缺失时给出明确告警。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib
matplotlib.use("Agg")                      # 无界面后端，便于在脚本/CI 中出图
import matplotlib.pyplot as plt
from matplotlib import font_manager
from wordcloud import WordCloud

from . import config
from .analyzer import DanmakuStats

logger = logging.getLogger(__name__)


def _use_chinese_font() -> str:
    """注册并启用中文字体，返回实际使用的字体名。"""
    if config.FONT_PATH:
        try:
            font_manager.fontManager.addfont(config.FONT_PATH)
            name = font_manager.FontProperties(fname=config.FONT_PATH).get_name()
            plt.rcParams["font.sans-serif"] = [name, "SimHei", "Microsoft YaHei"]
            plt.rcParams["font.family"] = "sans-serif"
            plt.rcParams["axes.unicode_minus"] = False
            logger.info("使用中文字体：%s（%s）", name, config.FONT_PATH)
            return name
        except Exception as exc:  # noqa: BLE001
            logger.warning("中文字体注册失败：%s", exc)
    logger.warning("未找到中文字体，图表中文可能显示为方框")
    return "sans-serif"


def _apply_style() -> None:
    """切换绘图样式后**重新应用中文字体**。

    坑点：``plt.style.use()`` 会把 rcParams 重置回样式默认值（字体变回 Arial），
    导致中文渲染成方框并把日志刷满 "Glyph missing from font(s) Arial" 警告。
    因此任何调用 plt.style.use 之后都必须重新注册中文字体。
    """
    style = config.CHART_STYLE
    plt.style.use(style if style in plt.style.available else "default")
    _use_chinese_font()


def make_wordcloud(
    word_freq: Sequence[Tuple[str, int]],
    path: Path = config.WORDCLOUD_FILE,
    mask_image: Optional[str] = None,
) -> Optional[Path]:
    """生成词云图。

    Args:
        word_freq: [(词, 词频)] 序列。
        path: 输出 PNG 路径。
        mask_image: 可选蒙版图片路径（用形状让词云更美观）。

    Returns:
        成功时返回路径，数据为空或字体缺失时返回 None。
    """
    if not word_freq:
        logger.warning("词频为空，跳过词云生成")
        return None

    freq = {str(w): int(c) for w, c in word_freq if w and int(c) > 0}
    if not freq:
        logger.warning("词频全为 0，跳过词云生成")
        return None

    mask = None
    if mask_image and Path(mask_image).exists():
        try:
            import numpy as np
            from PIL import Image

            mask = np.array(Image.open(mask_image).convert("L"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("蒙版图读取失败，改用矩形词云：%s", exc)
            mask = None

    try:
        cloud = WordCloud(
            font_path=config.FONT_PATH or None,
            width=config.WORDCLOUD_SIZE[0],
            height=config.WORDCLOUD_SIZE[1],
            background_color=config.WORDCLOUD_BG,
            colormap=config.WORDCLOUD_COLORMAP,
            max_words=config.WORDCLOUD_MAX_WORDS,
            prefer_horizontal=0.9,
            mask=mask,
            contour_width=1 if mask is not None else 0,
            contour_color="#00d1ff",
            relative_scaling=0.35,
            collocations=False,
        ).generate_from_frequencies(freq)

        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        plt.figure(figsize=(16, 9), dpi=config.CHART_DPI)
        plt.imshow(cloud, interpolation="bilinear")
        plt.axis("off")
        plt.tight_layout(pad=0)
        plt.savefig(out, bbox_inches="tight", facecolor=config.WORDCLOUD_BG)
        plt.close()
        logger.info("词云已生成：%s", out)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.error("词云生成失败：%s", exc)
        return None


def _bar_chart(
    labels: Sequence[str],
    values: Sequence[int],
    title: str,
    path: Path,
    xlabel: str = "",
    color: str = "#2E86DE",
    figsize: Tuple[int, int] = (12, 7),
    horizontal: bool = False,
) -> Optional[Path]:
    """画一个通用柱状图（内部复用，避免重复代码）。"""
    if not labels:
        logger.warning("图表 %s 无数据，跳过", title)
        return None
    _apply_style()
    plt.rcParams["axes.unicode_minus"] = False

    fig, ax = plt.subplots(figsize=figsize)
    if horizontal:
        bars = ax.barh(list(labels)[::-1], list(values)[::-1], color=color)
        ax.set_xlabel(xlabel or "次数")
    else:
        bars = ax.bar(list(labels), list(values), color=color)
        ax.set_ylabel(xlabel or "次数")
        plt.xticks(rotation=30, ha="right")
    ax.set_title(title, fontsize=15, fontweight="bold", pad=14)
    for bar in bars:
        width = bar.get_width()
        if horizontal:
            ax.text(width * 1.01, bar.get_y() + bar.get_height() / 2,
                    f"{int(width)}", va="center", fontsize=9)
        else:
            ax.text(bar.get_x() + bar.get_width() / 2, width * 1.01,
                    f"{int(width)}", ha="center", fontsize=9)
    ax.margins(y=0.12)
    fig.tight_layout()
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=config.CHART_DPI, bbox_inches="tight")
    plt.close(fig)
    logger.info("图表已生成：%s", out)
    return out


def make_top_comments_chart(stats: DanmakuStats, top_n: int = config.TOP_COMMENT_N) -> Optional[Path]:
    """Top-N 弹幕柱状图（作业要求的前 8 项）。

    数据说明：弹幕是高度长尾的文本，小样本下大部分弹幕只出现 1 次。
    若全局 Top-N 的最大频次仅为 1，图表会退化成"8 个并列的 1"而失去意义，
    此时改用**与 LLM 强相关的弹幕**（按出现次数排序），更能体现"哪类弹幕最集中"。
    """
    items = list(stats.top_comments[:top_n])
    fallback_used = False
    if not items or max(c for _, c in items) <= 1:
        llm_items = list(stats.top_llm_comments[:top_n])
        if llm_items and max(c for _, c in llm_items) > 1:
            items, fallback_used = llm_items, True
    if not items:
        return None

    subtitle = "（与 LLM 强相关弹幕）" if fallback_used else ""
    labels = [t if len(t) <= 14 else t[:13] + "…" for t, _ in items]
    return _bar_chart(labels, [c for _, c in items],
                      f"弹幕出现次数 Top{len(items)}{subtitle}",
                      config.CHART_DIR / "top_comments.png",
                      xlabel="出现次数", color="#EE5A6F", horizontal=True)


def make_top_words_chart(stats: DanmakuStats, top_n: int = 20) -> Optional[Path]:
    """高频词 Top-N 柱状图。"""
    items = stats.top_words[:top_n]
    if not items:
        return None
    labels = [w if len(w) <= 12 else w[:11] + "…" for w, _ in items]
    return _bar_chart(labels, [c for _, c in items],
                      f"弹幕高频词 Top{len(items)}", config.CHART_DIR / "top_words.png",
                      xlabel="词频", color="#10AC84", horizontal=True)


def make_domain_chart(stats: DanmakuStats) -> Optional[Path]:
    """应用领域分布柱状图。"""
    items = stats.domain_top(10)
    if not items:
        return None
    return _bar_chart([d for d, _ in items], [c for _, c in items],
                      "大语言模型应用领域分布", config.CHART_DIR / "domains.png",
                      xlabel="提及弹幕数", color="#5F27CD")


def make_sentiment_pie(stats: DanmakuStats) -> Optional[Path]:
    """情感倾向饼图。"""
    values = [stats.positive_count, stats.negative_count, stats.neutral_count]
    if sum(values) == 0:
        return None
    _apply_style()
    fig, ax = plt.subplots(figsize=(9, 7))
    wedges, texts, autotexts = ax.pie(
        values, labels=["正面", "负面", "中性"], autopct="%1.1f%%",
        colors=["#10AC84", "#EE5A6F", "#8395A7"], startangle=110,
        explode=(0.03, 0.03, 0.0), textprops={"fontsize": 12},
    )
    for at in autotexts:
        at.set_color("white")
        at.set_fontweight("bold")
    ax.set_title("B站用户对 LLM 的态度分布", fontsize=15, fontweight="bold", pad=16)
    fig.tight_layout()
    out = config.CHART_DIR / "sentiment.png"
    fig.savefig(out, dpi=config.CHART_DPI, bbox_inches="tight")
    plt.close(fig)
    logger.info("图表已生成：%s", out)
    return out


def make_progress_chart(stats: DanmakuStats) -> Optional[Path]:
    """弹幕在视频时间轴上的分布折线图（判断"哪个环节最受关注"）。"""
    if not stats.progress_buckets:
        logger.warning("时间分布数据为空，跳过时间轴图表")
        return None
    _apply_style()
    labels = [k for k, _ in stats.progress_buckets]
    values = [v for _, v in stats.progress_buckets]
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(labels, values, marker="o", linewidth=2.4, color="#FF9F43")
    ax.fill_between(range(len(labels)), values, alpha=0.22, color="#FF9F43")
    ax.set_title("弹幕在视频时间轴上的分布", fontsize=15, fontweight="bold", pad=14)
    ax.set_xlabel("视频进度")
    ax.set_ylabel("弹幕数")
    ax.grid(alpha=0.3)
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    out = config.CHART_DIR / "progress.png"
    fig.savefig(out, dpi=config.CHART_DPI, bbox_inches="tight")
    plt.close(fig)
    logger.info("图表已生成：%s", out)
    return out


def make_keyword_chart(stats: DanmakuStats) -> Optional[Path]:
    """成本 / 风险 / 收益三类关注点对比图。"""
    labels = ["成本价格", "风险隐患", "效率收益"]
    values = [stats.cost_count, stats.risk_count, stats.benefit_count]
    if sum(values) == 0:
        return None
    return _bar_chart(labels, values, "用户关注点分布",
                      config.CHART_DIR / "concerns.png",
                      xlabel="弹幕数", color="#0ABDE3", figsize=(9, 6))


def make_all_charts(stats: DanmakuStats) -> Dict[str, Optional[Path]]:
    """批量出图，返回 {名称: 路径}；单个失败不影响其他图。"""
    _use_chinese_font()
    charts: Dict[str, Optional[Path]] = {}
    for name, func in (
        ("top_comments", make_top_comments_chart),
        ("top_words", make_top_words_chart),
        ("domains", make_domain_chart),
        ("sentiment", make_sentiment_pie),
        ("progress", make_progress_chart),
        ("concerns", make_keyword_chart),
    ):
        try:
            charts[name] = func(stats)
        except Exception as exc:  # noqa: BLE001
            logger.error("生成图表 %s 失败：%s", name, exc)
            charts[name] = None
    charts["wordcloud"] = make_wordcloud(stats.top_words)
    return charts
