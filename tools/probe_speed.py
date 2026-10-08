# -*- coding: utf-8 -*-
"""验证时间预算保护后的抓取速率。"""
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\software\102401324\src")

from llm_danmaku.bili_client import BiliClient
from llm_danmaku.danmaku_crawler import DanmakuCrawler
from llm_danmaku.video_search import VideoInfo

ROOT = Path(r"D:\software\102401324")
cached = {f.stem for f in (ROOT / "data" / "raw").glob("BV*.jsonl")}
videos = [VideoInfo(**{k: v for k, v in item.items() if k in VideoInfo.__dataclass_fields__})
          for item in json.loads((ROOT / "data" / "videos.json").read_text(encoding="utf-8"))]
uncached = [v for v in videos if v.bvid not in cached]

sample = uncached[:10]
print(f"测试 {len(sample)} 个未缓存视频，时间预算 45s/视频\n", flush=True)

client = BiliClient()
crawler = DanmakuCrawler(client)

t0 = time.perf_counter()
results = crawler.crawl_all(sample, progress_every=5)
wall = time.perf_counter() - t0

ok = sum(1 for r in results if r.ok)
total = sum(r.count for r in results)
rate = wall / max(1, len(sample))
print(f"\n耗时 {wall:.1f}s，平均 {rate:.2f}s/视频", flush=True)
print(f"成功 {ok}/{len(sample)}，弹幕 {total} 条", flush=True)
print(f"剩余 {len(uncached)} 个预计需 {rate * len(uncached) / 60:.1f} 分钟", flush=True)
client.close()
