# -*- coding: utf-8 -*-
"""对比测试：同一批未缓存视频，串行 vs 6 线程并发请求 player/pagelist。"""
import json
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\software\102401324\src")

import requests

ROOT = Path(r"D:\software\102401324")
cached = {f.stem for f in (ROOT / "data" / "raw").glob("BV*.jsonl")}
videos = json.loads((ROOT / "data" / "videos.json").read_text(encoding="utf-8"))
sample = [v for v in videos if v["bvid"] not in cached][:10]
bvids = [v["bvid"] for v in sample]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
URL = "https://api.bilibili.com/x/player/pagelist"


def fetch(session, bvid, out, idx):
    """请求一个视频的 pagelist，记录结果。"""
    t0 = time.perf_counter()
    try:
        r = session.get(URL, params={"bvid": bvid}, timeout=20)
        out[idx] = (r.status_code, time.perf_counter() - t0, len(r.text))
    except Exception as exc:
        out[idx] = (f"{type(exc).__name__}", time.perf_counter() - t0, 0)


print("=== A. 串行（1 个共享 Session）===")
s = requests.Session()
s.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
out = {}
t0 = time.perf_counter()
for i, bv in enumerate(bvids):
    fetch(s, bv, out, i)
    print(f"  {bv}  {out[i][0]}  {out[i][1]:.2f}s", flush=True)
print(f"  串行总耗时 {time.perf_counter() - t0:.2f}s\n", flush=True)
s.close()

print("=== B. 6 线程并发（各自独立 Session）===")
out2 = {}
t0 = time.perf_counter()
threads = []
for i, bv in enumerate(bvids):
    def run(idx=i, b=bv):
        sess = requests.Session()
        sess.headers.update({"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        try:
            fetch(sess, b, out2, idx)
        finally:
            sess.close()
    t = threading.Thread(target=run)
    threads.append(t)
    t.start()
    if len(threads) % 6 == 0:
        for t2 in threads:
            t2.join()
        threads = []
for t2 in threads:
    t2.join()
wall = time.perf_counter() - t0
for i, bv in enumerate(bvids):
    print(f"  {bv}  {out2[i][0]}  {out2[i][1]:.2f}s", flush=True)
print(f"  并发总耗时 {wall:.2f}s", flush=True)
