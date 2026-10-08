# -*- coding: utf-8 -*-
"""打印统计摘要，供撰写博客与结论引用。"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

stats = json.loads(Path(r"D:\software\102401324\output\danmaku_stats.json")
                   .read_text(encoding="utf-8"))

print("=" * 62)
print("数据总览")
print("=" * 62)
print(f"  视频数        : {stats['video_count']}")
print(f"  抓取失败      : {stats['failed_videos']}")
print(f"  原始弹幕      : {stats['total_raw']:,}")
print(f"  视频内去重后  : {stats['total_cleaned']:,}")
print(f"  跨视频唯一种类: {stats['total_unique']:,}")
print(f"  数据质量      : {stats['clean_report']}")

print("\n" + "=" * 62)
print("数量排名前 8 的弹幕（跨视频出现次数）")
print("=" * 62)
for i, (text, count) in enumerate(stats["top_comments"], 1):
    print(f"  {i}. [{count:>3} 次] {text}")

print("\n" + "=" * 62)
print("与 LLM 强相关的 Top8 弹幕")
print("=" * 62)
for i, (text, count) in enumerate(stats["top_llm_comments"], 1):
    print(f"  {i}. [{count:>3} 次] {text}")

print("\n" + "=" * 62)
print("高频词 Top 30")
print("=" * 62)
words = stats["top_words"][:30]
print("  " + "  ".join(f"{w}({c})" for w, c in words))

print("\n" + "=" * 62)
print("应用领域分布")
print("=" * 62)
for domain, count in sorted(stats["domain_counts"].items(), key=lambda kv: -kv[1]):
    print(f"  {domain:<12} {count:>5} 条")

print("\n" + "=" * 62)
print("态度与关注点")
print("=" * 62)
total = stats["positive_count"] + stats["negative_count"] + stats["neutral_count"]
print(f"  正面 {stats['positive_count']:>5} ({stats['positive_count']/total*100:.1f}%)")
print(f"  负面 {stats['negative_count']:>5} ({stats['negative_count']/total*100:.1f}%)")
print(f"  中性 {stats['neutral_count']:>5} ({stats['neutral_count']/total*100:.1f}%)")
print(f"  提及成本/价格: {stats['cost_count']}")
print(f"  提及风险/隐患: {stats['risk_count']}")
print(f"  提及效率/收益: {stats['benefit_count']}")

print("\n" + "=" * 62)
print("弹幕在视频时间轴上的分布")
print("=" * 62)
for bucket, count in stats["progress_buckets"]:
    bar = "█" * max(1, int(count / max(1, max(c for _, c in stats["progress_buckets"])) * 40))
    print(f"  {bucket:<10} {count:>6}  {bar}")

print("\n" + "=" * 62)
print("视频内高频刷屏弹幕（复读机）Top 15")
print("=" * 62)
for text, count in stats["repeated_comments"][:15]:
    print(f"  [{count:>4} 次] {text}")

print("\n" + "=" * 62)
print("弹幕量最多的 10 个视频")
print("=" * 62)
for v in sorted(stats["video_stats"], key=lambda x: -x["raw_count"])[:10]:
    print(f"  {v['raw_count']:>5} 条  {v['title'][:44]}")
