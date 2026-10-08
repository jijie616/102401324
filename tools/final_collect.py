# -*- coding: utf-8 -*-
"""最终数据采集：长等待 + 增量抓取，直到拿到足够数据。

策略（针对 B 站突发风控优化）
-----------------------------
1. **先探测后动手**：只用 1 个轻量请求判断接口是否恢复，未恢复就安静等待；
2. **长等待**：每轮等待 15 分钟起步，逐步增加到 30 分钟，绝不在封禁期内反复冲击；
3. **增量抓取**：利用断点续爬缓存，每次只抓尚未成功的视频，越跑越接近 300；
4. **不删数据**：绝不清空已有缓存，任何一轮的收获都会累积保留。

跑完后会自动执行完整分析链路（clean/analyze/export/visualize/dashboard）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"D:\software\102401324")
SRC = ROOT / "src"
RAW = ROOT / "data" / "raw"
STATS = ROOT / "output" / "danmaku_stats.json"
VIDEOS = ROOT / "data" / "videos.json"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from llm_danmaku.bili_client import API_DANMAKU_XML, BiliClient   # noqa: E402
from llm_danmaku.danmaku_crawler import parse_danmaku_xml         # noqa: E402

TARGET_DANMAKU = 12000          # 认为"数据足够"的弹幕条数
MAX_ROUNDS = 24                 # 最多 24 轮（约 8-10 小时）


def api_alive() -> bool:
    """单请求轻量探测：接口是否恢复。"""
    client = BiliClient(delay_range=(0.0, 0.2), max_retries=1, min_interval=0.0)
    try:
        raw = client.get(API_DANMAKU_XML, {"oid": 137649199}, binary=True, retries=1)
        return len(parse_danmaku_xml(raw)) > 0
    except Exception as exc:  # noqa: BLE001
        print(f"    接口仍不可用：{type(exc).__name__}", flush=True)
        return False
    finally:
        client.close()


def local_stats() -> tuple:
    """返回 (已抓视频数, 弹幕总条数)。"""
    total = 0
    files = list(RAW.glob("BV*.jsonl"))
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                total += max(0, sum(1 for _ in fh) - 1)
        except OSError:
            pass
    return len(files), total


def stats_total() -> int:
    """从统计文件读弹幕总数（流水线跑完后可用）。"""
    try:
        return int(json.loads(STATS.read_text(encoding="utf-8"))["total_raw"])
    except Exception:
        return 0


def run_pipeline() -> None:
    """跑一次完整流水线（增量，不加 --force）。"""
    proc = subprocess.run(
        [sys.executable, "-u", "-m", "llm_danmaku.pipeline"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    for line in (proc.stdout or "").splitlines():
        if any(k in line for k in ("进度 ", "统计完成", "Excel", "全流程", "抓取结束")):
            print("   ", line, flush=True)


print("=" * 74, flush=True)
print("最终数据采集：长等待 + 增量抓取（先探测后动手，绝不清空已有数据）", flush=True)
print("=" * 74, flush=True)

for round_no in range(1, MAX_ROUNDS + 1):
    videos, danmaku = local_stats()
    print(f"\n{'─' * 74}", flush=True)
    print(f"[第 {round_no}/{MAX_ROUNDS} 轮] 本地已有 {videos} 个视频 / {danmaku} 条弹幕",
          flush=True)

    if danmaku >= TARGET_DANMAKU:
        print("  ✅ 数据已达标", flush=True)
        break

    if api_alive():
        print("  ✅ 接口可用，开始增量抓取", flush=True)
        run_pipeline()
        videos, danmaku = local_stats()
        got = max(danmaku, stats_total())
        print(f"  本轮结果：{videos} 个视频 / {got} 条弹幕", flush=True)
        if got >= TARGET_DANMAKU:
            print("\n🎉 数据采集完成", flush=True)
            break
        wait = 900
    else:
        # 未恢复：安静长等待，不发任何多余请求
        wait = min(1800, 900 + 300 * (round_no - 1))

    print(f"  等待 {wait // 60} 分钟后重试……", flush=True)
    time.sleep(wait)
else:
    print("\n⚠️ 轮次用尽", flush=True)

videos, danmaku = local_stats()
print(f"\n最终数据：{videos} 个视频 / {danmaku} 条弹幕", flush=True)

# 无论数据多少，最后都跑一次分析链路，保证产物齐全
print("\n执行最终分析链路……", flush=True)
run_pipeline()
print("\n完成。", flush=True)
