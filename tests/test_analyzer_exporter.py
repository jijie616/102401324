# -*- coding: utf-8 -*-
"""统计、导出与解析模块的单元测试（含网络层的替身测试）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from llm_danmaku.analyzer import (
    classify_domain,
    count_hits,
    analyze,
    _progress_bucket,
)
from llm_danmaku.cleaner import clean_batch
from llm_danmaku.danmaku_crawler import (
    Danmaku,
    CrawlResult,
    parse_danmaku_proto,
    parse_danmaku_xml,
)
from llm_danmaku.exporter import export_to_excel
from llm_danmaku.video_search import VideoInfo, parse_search_item


# ------------------------------------------------------------------ 公共夹具
@pytest.fixture()
def sample_results():
    """构造两个"视频"的抓取结果，供统计测试复用。"""
    first = CrawlResult(
        bvid="BV1TEST00001", title="大模型入门", ok=True,
        danmaku=[
            Danmaku("用GPT写代码效率真高", progress=10_000, mode=1),
            Danmaku("大模型会不会取代程序员", progress=65_000, mode=1),
            Danmaku("这个AI收费太贵了", progress=130_000, mode=1),
            Danmaku("用GPT写代码效率真高", progress=20_000, mode=1),
        ],
    )
    second = CrawlResult(
        bvid="BV1TEST00002", title="多模态科普", ok=True,
        danmaku=[
            Danmaku("多模态能力惊艳", progress=5_000, mode=1),
            Danmaku("担心隐私泄露问题", progress=200_000, mode=1),
            Danmaku("画图效果太好了", progress=400_000, mode=1),
        ],
    )
    failed = CrawlResult(bvid="BV1TEST00003", title="已删除", ok=False, error="404")
    return [first, second, failed]


# ---------------------------------------------------------------- TC15
def test_classify_domain_分类(sample_results):
    """TC15：应用领域分类应命中正确类别，无关键词返回 None。"""
    assert classify_domain("用GPT写代码效率真高") == "编程开发"
    assert classify_domain("画图效果太好了") == "绘画设计"
    assert classify_domain("今天天气不错") is None


# ---------------------------------------------------------------- TC16
def test_progress_bucket_边界():
    """TC16：时间桶边界——每桶 6 分钟；0ms 归 0-10%，恰好 6 分钟归 10-20%，超长封顶 90-100%。"""
    assert _progress_bucket(0) == "0-10%"
    assert _progress_bucket(359_999) == "0-10%"          # 差 1ms 到 6 分钟
    assert _progress_bucket(360_000) == "10-20%"         # 恰好 6 分钟
    assert _progress_bucket(99_999_999) == "90-100%"     # 超长视频封顶
    assert _progress_bucket(-5) == "0-10%"               # 异常值兜底


# ---------------------------------------------------------------- TC17
def test_count_hits_关键词命中():
    """TC17：关键词命中计数不区分大小写，且能正确处理零命中。"""
    texts = ["GPT真好用", "gpt收费吗", "无关内容"]
    assert count_hits(texts, ["gpt"]) == 2
    assert count_hits(texts, ["不存在的词"]) == 0
    assert count_hits([], ["gpt"]) == 0


# ---------------------------------------------------------------- TC18
def test_analyze_统计正确性(sample_results):
    """TC18：Top 弹幕按词频降序、取前 8；总量/唯一数/情感计数要正确。"""
    raw_texts = [dm.text for r in sample_results for dm in r.danmaku]
    kept, report = clean_batch(raw_texts)
    stats = analyze(sample_results, kept, report, top_comment_n=8, top_word_n=30)

    # 总量：失败视频也计入 video_count，但弹幕数为 0
    assert stats.total_raw == 7
    assert stats.total_cleaned == len(kept)
    assert stats.video_count == 3
    assert stats.failed_videos == 1

    # 去掉重复后，"用GPT写代码效率真高" 只出现一次，不应成为最高频
    assert len(stats.top_comments) <= 8
    counts = [c for _, c in stats.top_comments]
    assert counts == sorted(counts, reverse=True)

    # 领域分布应包含编程开发与绘画设计
    assert stats.domain_counts.get("编程开发", 0) >= 1
    assert stats.cost_count >= 1         # "收费太贵"
    assert stats.risk_count >= 1         # "取代程序员" / "隐私泄露"
    assert stats.positive_count + stats.negative_count + stats.neutral_count == len(kept)
    assert stats.progress_buckets, "时间分布不应为空"


# ---------------------------------------------------------------- TC19
def test_analyze_空输入不崩溃():
    """TC19：空数据集应返回零值统计而不是抛异常（异常处理要求）。"""
    stats = analyze([], [], None)
    assert stats.total_raw == 0
    assert stats.top_comments == []
    assert stats.positive_ratio == 0.0
    assert stats.domain_top() == []


# ---------------------------------------------------------------- TC20
def test_stats_save_and_shape(sample_results, tmp_path):
    """TC20：统计结果可落盘，JSON 中 tuple 被序列化为 list。"""
    from llm_danmaku.analyzer import DanmakuStats
    import json

    kept, report = clean_batch([dm.text for r in sample_results for dm in r.danmaku])
    stats = analyze(sample_results, kept, report)
    out = stats.save(tmp_path / "stats.json")

    data = json.loads(Path(out).read_text(encoding="utf-8"))
    assert data["video_count"] == 3
    assert isinstance(data["top_comments"], list)
    assert isinstance(data["video_stats"][0]["top_comments"], list)
    assert isinstance(data["domain_counts"], dict)


# ---------------------------------------------------------------- TC21
def test_export_to_excel(sample_results, tmp_path):
    """TC21：Excel 导出应生成文件并包含全部约定的 Sheet。"""
    openpyxl = pytest.importorskip("openpyxl")
    from openpyxl import load_workbook

    kept, report = clean_batch([dm.text for r in sample_results for dm in r.danmaku])
    stats = analyze(sample_results, kept, report)
    path = export_to_excel(stats, tmp_path / "out.xlsx")

    assert Path(path).exists()
    book = load_workbook(path)
    for sheet in ["数据总览", "Top弹幕", "词频统计", "视频清单", "领域分布"]:
        assert sheet in book.sheetnames

    ws = book["Top弹幕"]
    assert ws.cell(row=1, column=1).value == "弹幕内容"
    assert ws.max_row >= 2      # 至少有一行数据


# ---------------------------------------------------------------- TC22
def test_parse_search_item_容错():
    """TC22：搜索结果的解析应过滤无 bvid 的脏数据，并去掉高亮标签。"""
    good = parse_search_item(
        {"bvid": "BV1XX", "title": '<em class="keyword">大模型</em>入门',
         "author": "up", "play": "1000", "video_review": "88"}, "大模型")
    assert good is not None
    assert good.title == "大模型入门"
    assert good.play == 1000

    assert parse_search_item({"title": "没有bvid"}) is None
    assert parse_search_item({}) is None


# ---------------------------------------------------------------- TC23
def test_parse_danmaku_xml_解析():
    """TC23：弹幕 XML 解析应正确取出文本并换算毫秒时间点（含实体还原）。"""
    xml = (b'<?xml version="1.0" encoding="UTF-8"?><i><chatserver>chat</chatserver>'
           b'<d p="1.5,1,25,16777215,1600000000,0,abc,123">\xe5\xa4\xa7\xe6\xa8\xa1\xe5\x9e\x8b</d>'
           b'<d p="2.0,4,25,16777215,1600000001,0,def,124">A&amp;B</d></i>')
    items = parse_danmaku_xml(xml)
    assert len(items) == 2
    assert items[0].progress == 1500
    assert items[0].mode == 1
    assert items[0].text == "大模型"
    assert items[1].mode == 4
    assert items[1].text == "A&B"        # XML 实体应被还原


# ---------------------------------------------------------------- TC24
def test_parse_danmaku_xml_坏数据不抛异常():
    """TC24：非法 XML 与缺少 p 属性的脏数据应被跳过，而不是抛异常。"""
    assert parse_danmaku_xml(b"") == []
    # 正则路径能匹配到合法的 d，同时应跳过没有 p 属性的 d
    mixed = ('<i><d p="1.0,1,25,0,0,0,x,1">正常</d>'
             '<d>没有p属性</d></i>').encode("utf-8")
    items = parse_danmaku_xml(mixed)
    assert len(items) == 1
    assert items[0].text == "正常"
    # 完全不合法的内容应返回空列表
    assert parse_danmaku_xml("不是XML".encode("utf-8")) == []


# ---------------------------------------------------------------- TC25
def test_parse_danmaku_proto_手工构造():
    """TC25：手写 protobuf 解析器应能还原 elems 中的文本与时间点。

    手工编码一个 DmSegMobileReply：
      field 1 (elems, LEN) -> DanmakuElem
        field 2 (progress, varint) = 12345
        field 3 (mode, varint) = 1
        field 9 (content, LEN) = "大模型"
        field 11 (weight, varint) = 7
    """
    def varint(n: int) -> bytes:
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            out.append(b | (0x80 if n else 0))
            if not n:
                return bytes(out)

    content = "大模型".encode("utf-8")
    elem = (b"\x10" + varint(12345) +            # field2 progress
            b"\x18" + varint(1) +                # field3 mode
            b"\x4a" + varint(len(content)) + content +   # field9 content
            b"\x58" + varint(7))                 # field11 weight
    payload = b"\x0a" + varint(len(elem)) + elem  # field1 elems

    items = parse_danmaku_proto(payload)
    assert len(items) == 1
    assert items[0].text == "大模型"
    assert items[0].progress == 12345
    assert items[0].mode == 1
    assert items[0].weight == 7

    assert parse_danmaku_proto(b"") == []
    assert parse_danmaku_proto(b"\xff\xff\xff") == []   # 损坏数据


# ---------------------------------------------------------------- TC26
def test_wbi_load_state_可变性():
    """TC26：VideoInfo 默认值互不共享（避免可变默认参数导致的数据串味）。"""
    a, b = VideoInfo(bvid="BV1", title="t1"), VideoInfo(bvid="BV2", title="t2")
    a.tags.append("x")
    assert b.tags == []
    assert a.as_dict()["bvid"] == "BV1"
