# -*- coding: utf-8 -*-
"""等待主爬虫完成，然后自动跑完整分析链路。

用途：主爬虫是长时间后台任务，本脚本轮询缓存文件数量，当数量停止增长
（或达到 300）后，触发 clean -> analyze -> export -> visualize -> dashboard。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(r"D:\software\102401324")
RAW = ROOT / "data" / "raw"
TARGET = 300


def count_cached() -> int:
    """已缓存的视频数。"""
    return len(list(RAW.glob("BV*.jsonl")))


def main() -> int:
    """轮询等待后执行分析链路。"""
    last = count_cached()
    stable_rounds = 0
    print(f"起始缓存数：{last}", flush=True)

    # 最多等 3 小时；连续 8 次（每次 45 秒）无增长视为爬虫结束
    for round_no in range(1, 241):
        time.sleep(45)
        now = count_cached()
        delta = now - last
        print(f"[{round_no:>3}] 缓存 {now}/{TARGET}（+{delta}）", flush=True)
        last = now

        if now >= TARGET:
            print("已达到 300 个视频", flush=True)
            break
        if delta == 0:
            stable_rounds += 1
            if stable_rounds >= 8:
                print("连续 6 分钟无新增，判定爬取结束", flush=True)
                break
        else:
            stable_rounds = 0
    else:
        print("等待超时（3 小时）", flush=True)

    print("\n=== 开始跑分析链路 ===", flush=True)
    result = subprocess.run(
        [sys.executable, "-m", "llm_danmaku.pipeline",
         "--stages", "clean,analyze,export,visualize,dashboard"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(result.stdout[-6000:], flush=True)
    if result.returncode != 0:
        print("STDERR:", result.stderr[-3000:], flush=True)
    print(f"\n分析链路退出码：{result.returncode}", flush=True)
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
