# -*- coding: utf-8 -*-
"""弹幕分析流水线统一入口（CLI）。

用法：
    python -m llm_danmaku.pipeline                    # 全流程
    python -m llm_danmaku.pipeline --stage search     # 只跑某一步
    python -m llm_danmaku.pipeline --stages crawl,clean,analyze
    python -m llm_danmaku.pipeline --limit 20         # 小样验证
    python -m llm_danmaku.pipeline --force            # 忽略缓存重新抓取

分步执行的目的是让"代码有进展即签入"落地：每完成一个功能即可单独验证与提交。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from . import config
from .analyzer import DanmakuStats, VideoStat, analyze
from .bili_client import BiliClient
from .cleaner import CleanReport, clean_batch
from .danmaku_crawler import CrawlResult, DanmakuCrawler
from .exporter import export_to_excel, export_videos_json
from .video_search import VideoInfo, search_videos

logger = logging.getLogger("llm_danmaku.pipeline")

ALL_STAGES = ["search", "crawl", "clean", "analyze", "export", "visualize", "dashboard"]


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------
def setup_logging(level: str = "INFO") -> None:
    """统一日志格式：同时输出到控制台与 logs/run.log。"""
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"
    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(config.LOG_DIR / "run.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, handlers=handlers, force=True)
    # 第三方库降噪
    for noisy in ("urllib3", "matplotlib", "PIL", "jieba"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------
# 各阶段
# --------------------------------------------------------------------------
def stage_search(limit: Optional[int], client: BiliClient) -> List[VideoInfo]:
    """阶段 1：检索视频列表。"""
    target = limit or config.TARGET_VIDEO_COUNT
    logger.info("开始检索视频，目标 %d 个，关键词 %s", target, config.KEYWORDS)
    videos = search_videos(client, config.KEYWORDS, target)
    if not videos:
        raise RuntimeError("未检索到任何视频，请检查网络或关键词")
    export_videos_json(videos)
    logger.info("视频检索完成：%d 个，已写入 %s", len(videos), config.VIDEO_LIST_FILE)
    return videos


def load_videos() -> List[VideoInfo]:
    """从 data/videos.json 读回视频列表。"""
    if not config.VIDEO_LIST_FILE.exists():
        raise FileNotFoundError(
            f"{config.VIDEO_LIST_FILE} 不存在，请先执行 search 阶段")
    with open(config.VIDEO_LIST_FILE, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return [VideoInfo(**{k: v for k, v in item.items()
                         if k in VideoInfo.__dataclass_fields__}) for item in raw]


def stage_crawl(videos: Sequence[VideoInfo], client: BiliClient,
                force: bool = False) -> List[CrawlResult]:
    """阶段 2：抓取弹幕（断点续爬）。"""
    crawler = DanmakuCrawler(client)
    logger.info("开始抓取弹幕，共 %d 个视频，并发 %d", len(videos), crawler.workers)
    return crawler.crawl_all(videos, force=force)


def stage_clean(results: Sequence[CrawlResult]) -> tuple:
    """阶段 3：清洗并落盘。

    Returns:
        (清洗后文本列表, 清洗报告)
    """
    all_texts = [dm.text for res in results for dm in res.danmaku]
    cleaned, report = clean_batch(all_texts)

    config.CLEANED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(config.CLEANED_FILE, "w", encoding="utf-8") as fh:
        for text in cleaned:
            fh.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")

    # 每类弹幕（按来源视频）单独落盘，便于抽查与测试
    per_video_dir = config.DATA_DIR / "cleaned_by_video"
    per_video_dir.mkdir(parents=True, exist_ok=True)
    for res in results:
        texts = [dm.text for dm in res.danmaku]
        sub_cleaned, _ = clean_batch(texts, deduplicate=False)
        with open(per_video_dir / f"{res.bvid}.txt", "w", encoding="utf-8") as fh:
            fh.write("\n".join(sub_cleaned))

    logger.info("清洗结果已写入 %s（%d 条）", config.CLEANED_FILE, len(cleaned))
    return cleaned, report


def stage_analyze(results: Sequence[CrawlResult], cleaned: Sequence[str],
                  report: CleanReport) -> DanmakuStats:
    """阶段 4：统计分析。"""
    stats = analyze(results, cleaned, report)
    stats.save()
    return stats


def stage_export(stats: DanmakuStats) -> Path:
    """阶段 5：导出 xlsx。"""
    return export_to_excel(stats)


def stage_visualize(stats: DanmakuStats) -> Dict[str, Optional[Path]]:
    """阶段 6：词云与图表。"""
    from .visualizer import make_all_charts

    charts = make_all_charts(stats)
    ok = sum(1 for v in charts.values() if v)
    logger.info("可视化完成：%d/%d 张图", ok, len(charts))
    return charts


def stage_dashboard(stats: DanmakuStats) -> Optional[Path]:
    """阶段 7：可视化大屏。"""
    from .dashboard import build_dashboard

    return build_dashboard(stats)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def run_pipeline(stages: Sequence[str], limit: Optional[int] = None,
                 force: bool = False) -> Dict[str, object]:
    """按顺序执行指定阶段，中间结果在内存中传递。

    Args:
        stages: 要执行的阶段名列表。
        limit: 限制视频数量（小样验证用）。
        force: 忽略弹幕缓存。

    Returns:
        各阶段的关键产物（路径或对象）。
    """
    started = time.perf_counter()
    artifacts: Dict[str, object] = {}
    client = BiliClient()
    videos: List[VideoInfo] = []
    results: List[CrawlResult] = []
    cleaned: List[str] = []
    report: Optional[CleanReport] = None
    stats: Optional[DanmakuStats] = None

    try:
        for stage in stages:
            t0 = time.perf_counter()
            logger.info("========== 阶段开始：%s ==========", stage)
            if stage == "search":
                videos = stage_search(limit, client)
                artifacts["videos"] = config.VIDEO_LIST_FILE
            elif stage == "crawl":
                videos = videos or load_videos()
                if limit:
                    videos = videos[:limit]
                results = stage_crawl(videos, client, force=force)
                artifacts["crawl"] = sum(r.count for r in results)
            elif stage == "clean":
                if not results:
                    results = load_cached_results(videos or load_videos())
                cleaned, report = stage_clean(results)
                artifacts["cleaned"] = config.CLEANED_FILE
            elif stage == "analyze":
                if not results:
                    results = load_cached_results(videos or load_videos())
                if not cleaned:
                    cleaned, report = load_cleaned()
                stats = stage_analyze(results, cleaned, report or CleanReport())
                artifacts["stats"] = config.STATS_FILE
            elif stage in ("export", "visualize", "dashboard"):
                if stats is None:
                    stats = load_stats()
                artifacts["excel" if stage == "export" else
                          ("charts" if stage == "visualize" else "dashboard")] = (
                    stage_export(stats) if stage == "export" else
                    (stage_visualize(stats) if stage == "visualize" else stage_dashboard(stats))
                )
                if stage == "visualize":
                    artifacts["charts"] = stage_visualize(stats)
                else:
                    artifacts["dashboard"] = stage_dashboard(stats)
            else:
                raise ValueError(f"未知阶段：{stage}（可选：{', '.join(ALL_STAGES)}）")
            logger.info("========== 阶段完成：%s（%.1fs）==========",
                        stage, time.perf_counter() - t0)
    finally:
        client.close()

    logger.info("全流程耗时 %.1fs", time.perf_counter() - started)
    return artifacts


def load_cached_results(videos: Sequence[VideoInfo]) -> List[CrawlResult]:
    """直接从 data/raw 读回已抓取的弹幕（跳过 crawl 阶段时使用）。"""
    crawler = DanmakuCrawler(BiliClient())
    results: List[CrawlResult] = []
    missing = 0
    for v in videos:
        cache = config.RAW_DIR / f"{v.bvid}.jsonl"
        if not cache.exists():
            missing += 1
            continue
        loaded = crawler._load_cache(cache)            # noqa: SLF001 - 内部复用
        if loaded:
            loaded.title = loaded.title or v.title
            results.append(loaded)
    if missing:
        logger.warning("%d 个视频没有本地缓存，已跳过（可先跑 crawl 阶段）", missing)
    logger.info("从缓存读回 %d 个视频的弹幕，共 %d 条",
                len(results), sum(r.count for r in results))
    return results


def load_stats() -> DanmakuStats:
    """从 output/danmaku_stats.json 读回统计结果（跳过 analyze 阶段时使用）。"""
    if not config.STATS_FILE.exists():
        raise FileNotFoundError(f"{config.STATS_FILE} 不存在，请先执行 analyze 阶段")
    with open(config.STATS_FILE, "r", encoding="utf-8") as fh:
        raw = json.load(fh)

    # tuples 在 JSON 中变成 list，这里还原成统计模块期望的结构
    video_stats = []
    for item in raw.get("video_stats", []):
        item = dict(item)
        item["top_comments"] = [tuple(x) for x in item.get("top_comments", [])]
        video_stats.append(VideoStat(**item))

    def _pairs(key: str):
        return [tuple(x) for x in raw.get(key, [])]

    return DanmakuStats(
        total_raw=raw.get("total_raw", 0),
        total_cleaned=raw.get("total_cleaned", 0),
        total_unique=raw.get("total_unique", 0),
        video_count=raw.get("video_count", 0),
        failed_videos=raw.get("failed_videos", 0),
        clean_report=raw.get("clean_report", {}),
        top_comments=_pairs("top_comments"),
        top_llm_comments=_pairs("top_llm_comments"),
        top_words=_pairs("top_words"),
        repeated_comments=_pairs("repeated_comments"),
        video_stats=video_stats,
        domain_counts=raw.get("domain_counts", {}),
        cost_count=raw.get("cost_count", 0),
        risk_count=raw.get("risk_count", 0),
        benefit_count=raw.get("benefit_count", 0),
        positive_count=raw.get("positive_count", 0),
        negative_count=raw.get("negative_count", 0),
        neutral_count=raw.get("neutral_count", 0),
        progress_buckets=_pairs("progress_buckets"),
        generated_at=raw.get("generated_at", ""),
    )


def load_cleaned() -> tuple:
    """从 data/danmaku_cleaned.jsonl 读回清洗结果。"""
    if not config.CLEANED_FILE.exists():
        raise FileNotFoundError(f"{config.CLEANED_FILE} 不存在，请先执行 clean 阶段")
    texts: List[str] = []
    with open(config.CLEANED_FILE, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                texts.append(json.loads(line)["text"])
    return texts, CleanReport(total=len(texts), kept=len(texts))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="B站大语言模型相关视频弹幕分析流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--stages", default=",".join(ALL_STAGES),
                        help=f"要执行的阶段，逗号分隔（可选：{','.join(ALL_STAGES)}）")
    parser.add_argument("--stage", dest="single_stage", default=None,
                        help="只执行单个阶段（等价于 --stages 只写一个）")
    parser.add_argument("--limit", type=int, default=0, help="限制视频数量（0 表示不限制）")
    parser.add_argument("--force", action="store_true", help="忽略弹幕缓存重新抓取")
    parser.add_argument("--log-level", default="INFO", help="日志级别")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI 入口。"""
    args = parse_args(argv)
    setup_logging(args.log_level)

    stages = [args.single_stage] if args.single_stage else \
        [s.strip() for s in args.stages.split(",") if s.strip()]
    invalid = [s for s in stages if s not in ALL_STAGES]
    if invalid:
        logger.error("未知阶段 %s，可选：%s", invalid, ", ".join(ALL_STAGES))
        return 2

    try:
        artifacts = run_pipeline(stages, limit=args.limit or None, force=args.force)
    except Exception as exc:  # noqa: BLE001 - CLI 顶层兜底
        logger.exception("流水线执行失败：%s", exc)
        return 1

    print("\n================ 产物汇总 ================")
    for key, value in artifacts.items():
        print(f"  {key:<10} {value}")
    print("==========================================")
    return 0


if __name__ == "__main__":
    sys.exit(main())
