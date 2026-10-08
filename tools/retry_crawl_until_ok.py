# -*- coding: utf-8 -*-
"""持续重试全量抓取，直到成功（用于等待 B 站突发限流解除）。

策略：
1. 反复执行完整流水线（search -> crawl -> clean -> analyze -> export -> visualize -> dashboard）；
2. 每轮结束后统计实际抓到的弹幕数，达到目标（默认 15000 条）即判定成功并退出；
3. 未达标则等待一段时间再试（等待时长逐轮递增，最多 20 分钟一轮）；
4. 最多尝试 12 轮（约 3 小时）。

这样即使遇到限流，也能在风控解除后自动补齐数据，不需要人工盯着。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"D:\software\102401324")
RAW = ROOT / "data" / "raw"
STATS = ROOT / "output" / "danmaku_stats.json"

TARGET_DANMAKU = 15000
MAX_ROUNDS = 12


def raw_count() -> tuple:
    """返回 (已抓视频数, 弹幕总条数)。"""
    files = list(RAW.glob("BV*.jsonl"))
    total = 0
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                total += max(0, sum(1 for _ in fh) - 1)
        except OSError:
            pass
    return len(files), total


def stats_total() -> int:
    """从统计文件读取弹幕总数（流水线完整跑完时可用）。"""
    try:
        return int(json.loads(STATS.read_text(encoding="utf-8"))["total_raw"])
    except Exception:
        return 0


for round_no in range(1, MAX_ROUNDS + 1):
    videos, danmaku = raw_count()
    print(f"\n{'=' * 70}", flush=True)
    print(f"第 {round_no}/{MAX_ROUNDS} 轮：当前 {videos} 个视频 / {danmaku} 条弹幕", flush=True)
    print(f"{'=' * 70}", flush=True)

    # 第 1 轮用 --force 全量抓取；后续轮次不加 --force，
    # 这样已成功的视频走缓存、只补齐缺失部分（增量抓取），避免反复冲击接口。
    cmd = [sys.executable, "-u", "-m", "llm_danmaku.pipeline"]
    if round_no == 1:
        cmd.append("--force")

    proc = subprocess.run(
        cmd, cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    tail = (proc.stdout or "")[-2500:]
    print(tail, flush=True)

    videos, danmaku = raw_count()
    got = max(danmaku, stats_total())
    print(f"\n本轮结束：{videos} 个视频 / {got} 条弹幕（目标 {TARGET_DANMAKU}）", flush=True)

    if got >= TARGET_DANMAKU:
        print("\n✅ 已达到目标数据量，抓取完成", flush=True)
        break

    wait = min(1200, 120 * round_no)
    print(f"数据不足，等待 {wait} 秒后重试……", flush=True)
    time.sleep(wait)
else:
    print("\n⚠️ 达到最大轮次仍未达标", flush=True)

videos, danmaku = raw_count()
print(f"\n最终：{videos} 个视频 / {danmaku} 条弹幕", flush=True)
