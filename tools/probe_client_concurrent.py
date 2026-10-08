# -*- coding: utf-8 -*-
"""最小复现：直接用 BiliClient 并发请求 pagelist，看是否卡住。"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\software\102401324\src")

import json
from pathlib import Path

from llm_danmaku.bili_client import API_PAGELIST, BiliClient

ROOT = Path(r"D:\software\102401324")
cached = {f.stem for f in (ROOT / "data" / "raw").glob("BV*.jsonl")}
videos = json.loads((ROOT / "data" / "videos.json").read_text(encoding="utf-8"))
bvids = [v["bvid"] for v in videos if v["bvid"] not in cached][:6]

client = BiliClient(delay_range=(0, 0))
print(f"测试 {len(bvids)} 个视频，6 线程并发，无随机延迟\n", flush=True)


def one(bvid: str) -> tuple:
    """单次请求，返回 (bvid, 结果, 耗时)。"""
    t0 = time.perf_counter()
    try:
        data = client.get_json(API_PAGELIST, {"bvid": bvid})
        n = len(data) if isinstance(data, list) else "?"
        return (bvid, f"OK {n}分P", time.perf_counter() - t0)
    except Exception as exc:
        return (bvid, f"FAIL {type(exc).__name__}: {str(exc)[:40]}", time.perf_counter() - t0)


t0 = time.perf_counter()
with ThreadPoolExecutor(max_workers=6) as pool:
    futures = [pool.submit(one, bv) for bv in bvids]
    for i, fut in enumerate(as_completed(futures, timeout=60), 1):
        bvid, status, dt = fut.result()
        print(f"  [{i}] {bvid}  {status}  {dt:.2f}s", flush=True)
print(f"\n总耗时 {time.perf_counter() - t0:.2f}s", flush=True)
client.close()
