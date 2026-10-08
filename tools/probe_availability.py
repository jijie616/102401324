# -*- coding: utf-8 -*-
"""检查 B 站弹幕接口当前可用性（判断风控是否解除）。"""
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"D:\software\102401324\src")

from llm_danmaku.bili_client import API_DANMAKU_XML, BiliClient
from llm_danmaku.danmaku_crawler import parse_danmaku_xml

CIDS = [137649199, 62131, 1176840, 1308288574]

c = BiliClient(delay_range=(2.0, 3.0), max_retries=1)
print("连续 4 次弹幕请求（间隔 2-3 秒，单次超时 20s）：\n", flush=True)
ok = 0
for cid in CIDS:
    t0 = time.perf_counter()
    try:
        raw = c.get(API_DANMAKU_XML, {"oid": cid}, binary=True, retries=1)
        items = parse_danmaku_xml(raw)
        ok += 1
        print(f"  ✅ cid={cid}  {time.perf_counter()-t0:.2f}s  {len(items)} 条弹幕", flush=True)
    except Exception as exc:
        print(f"  ❌ cid={cid}  {time.perf_counter()-t0:.2f}s  "
              f"{type(exc).__name__}: {str(exc)[:60]}", flush=True)
print(f"\n成功率 {ok}/{len(CIDS)}")
c.close()
