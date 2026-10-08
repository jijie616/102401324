# -*- coding: utf-8 -*-
"""性能分析：定位数据统计接口的瓶颈并输出可视化报告（作业 3.3 要求）。

用法：
    python -m llm_danmaku.profile_runner                 # 用真实数据
    python -m llm_danmaku.profile_runner --synthetic 50000  # 用合成数据压测

产物（output/profile/）：
    profile_stats.txt   cProfile 文本报告，按累计耗时排序
    profile_stats.prof  原始 profile 数据，可用 snakeviz 交互查看
    profile_chart.png   耗时 Top-N 函数柱状图，**可直接贴进博客**
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import logging
import pstats
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from . import config

logger = logging.getLogger(__name__)

# 合成数据用的词汇（模拟真实弹幕中的高频短语）
_FAKE_PHRASES = [
    "大模型幻觉太严重了", "用GPT写代码效率真高", "这个AI收费好贵啊", "会不会取代程序员",
    "多模态能力真强", "本地部署要显卡吧", "国产大模型加油", "免费额度根本不够用",
    "写论文用它太爽了", "担心隐私泄露", "客服已经被AI取代了", "画图效果惊艳",
    "训练成本太高", "推理速度有点慢", "开源模型也不错了", "这波降本增效稳了",
]
_FAKE_SUBJECTS = ["这个", "那个", "感觉", "其实", "说实话", "我个人觉得", "有一说一", ""]
_FAKE_TAILS = ["", "！", "？", "。。。", "哈哈哈", "，你们觉得呢", "，反正我信了", "吧"]
_FAKE_FILLERS = ["真的", "确实", "可能", "大概", "应该", "基本上", "总的来说", ""]


def make_synthetic(n: int, seed: int = 42) -> List[str]:
    """生成 n 条合成弹幕，用于在没有网络/数据时压测统计性能。

    关键点：必须保证**文本多样性**。早期版本只用「短语 + 后缀」组合，
    5 万条样本实际只有 80 种唯一文本，导致去重后只剩 80 条，
    测出来的是"去重开销"而不是真实统计分布。现在叠加主语、副词等修饰，
    组合空间达到数万级，唯一率接近真实弹幕。
    """
    rng = random.Random(seed)
    seen: set = set()
    texts: List[str] = []
    while len(texts) < n:
        text = (f"{rng.choice(_FAKE_SUBJECTS)}{rng.choice(_FAKE_FILLERS)}"
                f"{rng.choice(_FAKE_PHRASES)}{rng.choice(_FAKE_TAILS)}")
        text = text.strip() or "大模型"
        # 允许少量重复（真实弹幕确实存在复读），但整体保持高唯一率
        if text in seen and rng.random() > 0.05:
            continue
        seen.add(text)
        texts.append(text)
    return texts


@dataclass
class Timing:
    """一个阶段的耗时记录。"""

    name: str
    seconds: float

    def __str__(self) -> str:
        return f"{self.name:<16} {self.seconds:8.3f}s"


def timeit(func: Callable[[], object], name: str = "") -> Tuple[object, Timing]:
    """执行函数并计时（用于模块级耗时对比）。"""
    start = time.perf_counter()
    result = func()
    return result, Timing(name or getattr(func, "__name__", "func"), time.perf_counter() - start)


def profile_pipeline(texts: Sequence[str], out_dir: Optional[Path] = None,
                     top_n: int = 15) -> Tuple[Path, List[Tuple[str, float]]]:
    """对"清洗 -> 分词 -> 统计"这条数据统计链路做 cProfile 分析。

    Args:
        texts: 待分析的弹幕文本。
        out_dir: 报告输出目录；为 None 时用 config.PROFILE_DIR
            （在函数体内取，避免默认参数在定义时求值）。
        top_n: 报告里展示的 Top 函数数量。

    Returns:
        (报告路径, [(函数名, 累计耗时秒)])  —— 后者用于画柱状图。
    """
    from .analyzer import analyze
    from .cleaner import clean_batch
    from .danmaku_crawler import CrawlResult, Danmaku

    out_dir = Path(out_dir) if out_dir is not None else config.PROFILE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    def workload() -> Counter:
        """被 profile 的目标：完整统计链路。"""
        cleaned, report = clean_batch(texts)
        fake_result = CrawlResult(
            bvid="SYNTHETIC", title="性能压测样本",
            danmaku=[Danmaku(text=t) for t in texts], ok=True,
        )
        stats = analyze([fake_result], cleaned, report)
        return Counter(dict(stats.top_words))

    profiler = cProfile.Profile()
    profiler.enable()
    workload()
    profiler.disable()

    prof_path = out_dir / "profile_stats.prof"
    profiler.dump_stats(str(prof_path))

    stream = io.StringIO()
    stats_obj = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
    stats_obj.print_stats(top_n * 3)
    text_report = out_dir / "profile_stats.txt"
    text_report.write_text(stream.getvalue(), encoding="utf-8")

    # 取耗时最高的函数（剔除内置与 profile 自身）
    timings: List[Tuple[str, float]] = []
    for (filename, _lineno, func_name), (_cc, _nc, _tt, cumtime, _callers) in \
            stats_obj.stats.items():
        if "profile_runner" in filename or func_name in {"<built-in method builtins.exec>",
                                                         "profile"}:
            continue
        short = Path(filename).name
        timings.append((f"{func_name} ({short})", cumtime))
    timings.sort(key=lambda kv: kv[1], reverse=True)
    timings = timings[:top_n]

    logger.info("cProfile 报告已写入 %s", text_report)
    return text_report, timings


def render_profile_chart(timings: Sequence[Tuple[str, float]],
                         path: Optional[Path] = None) -> Optional[Path]:
    """把耗时 Top-N 函数画成横向柱状图（博客图 3.3 直接用这张）。"""
    if not timings:
        return None
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager

        if config.FONT_PATH:
            font_manager.fontManager.addfont(config.FONT_PATH)
            name = font_manager.FontProperties(fname=config.FONT_PATH).get_name()
            plt.rcParams["font.sans-serif"] = [name, "SimHei"]

        labels = [t[0] if len(t[0]) <= 46 else t[0][:45] + "…" for t in timings][::-1]
        values = [t[1] for t in timings][::-1]

        fig, ax = plt.subplots(figsize=(12, 8))
        bars = ax.barh(labels, values, color="#4C6EF5")
        ax.set_title("数据统计链路耗时 Top 函数（cProfile 累计耗时）",
                     fontsize=14, fontweight="bold", pad=14)
        ax.set_xlabel("累计耗时（秒）")
        for bar in bars:
            w = bar.get_width()
            ax.text(w * 1.005, bar.get_y() + bar.get_height() / 2,
                    f"{w:.3f}s", va="center", fontsize=8)
        ax.margins(x=0.18)
        fig.tight_layout()

        out = Path(path or (config.PROFILE_DIR / "profile_chart.png"))
        fig.savefig(out, dpi=config.CHART_DPI, bbox_inches="tight")
        plt.close(fig)
        logger.info("性能分析图已生成：%s", out)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.error("性能分析图生成失败：%s", exc)
        return None


def load_real_texts(limit: Optional[int] = None) -> List[str]:
    """从清洗结果或原始缓存里读真实弹幕文本，用于真实数据 profile。"""
    texts: List[str] = []
    if config.CLEANED_FILE.exists():
        with open(config.CLEANED_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    texts.append(json.loads(line)["text"])
                if limit and len(texts) >= limit:
                    return texts
    if texts:
        return texts

    for raw_file in sorted(config.RAW_DIR.glob("BV*.jsonl")):
        with open(raw_file, "r", encoding="utf-8") as fh:
            next(fh, None)                      # 跳过元信息行
            for line in fh:
                line = line.strip()
                if line:
                    texts.append(json.loads(line)["text"])
                if limit and len(texts) >= limit:
                    return texts
    return texts


def main(argv: Optional[Sequence[str]] = None) -> int:
    """命令行入口。"""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="数据统计接口性能分析")
    parser.add_argument("--synthetic", type=int, default=0,
                        help="用 N 条合成弹幕压测（不读真实数据）")
    parser.add_argument("--limit", type=int, default=0, help="真实数据最多取 N 条")
    args = parser.parse_args(argv)

    if args.synthetic:
        texts = make_synthetic(args.synthetic)
        logger.info("使用合成数据 %d 条", len(texts))
    else:
        texts = load_real_texts(args.limit or None)
        if not texts:
            logger.warning("未找到真实弹幕数据，自动改用 30000 条合成数据")
            texts = make_synthetic(30000)
        else:
            logger.info("使用真实数据 %d 条", len(texts))

    # 分阶段计时：让博客里能写清"哪一步最慢"
    from .cleaner import clean_batch
    from .tokenizer import tokenize_batch

    print("\n=== 分阶段计时 ===")
    t0 = time.perf_counter()
    cleaned, report = clean_batch(texts)
    clean_seconds = time.perf_counter() - t0

    t0 = time.perf_counter()
    tokenize_batch(cleaned)
    token_seconds = time.perf_counter() - t0

    print(f"{'清洗(含去重)':<16}{clean_seconds:8.3f}s   （{len(texts)} 条 -> {len(cleaned)} 条）")
    print(f"{'分词':<16}{token_seconds:8.3f}s   （{len(cleaned)} 条有效弹幕）")

    report_path, timings = profile_pipeline(texts)
    chart = render_profile_chart(timings)

    print("\n=== 耗时 Top 函数（累计）===")
    for name, sec in timings[:10]:
        print(f"  {sec:8.3f}s  {name}")
    print(f"\ncProfile 报告: {report_path}")
    if chart:
        print(f"性能分析图  : {chart}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
