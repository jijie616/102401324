# -*- coding: utf-8 -*-
"""附加题模块的单元测试：媒体观点解析与学术趋势预测。

所有用例都不发真实网络请求——用内置的 RSS/Atom 样例字符串和构造的
Article/Paper 对象验证解析与统计逻辑。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from llm_danmaku.academic_trends import (
    Paper,
    build_report,
    compute_trend,
    dimension_by_month,
    month_windows,
    top_keywords,
)
from llm_danmaku.media_analyzer import (
    Article,
    _parse_date,
    build_forecast,
    dimension_stats,
    is_topic_relevant,
    parse_feed,
    trend_momentum,
)

# ------------------------------------------------------------------ 样例数据
RSS_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test Feed</title>
  <item>
    <title>GPT-5 \xe5\x8f\x91\xe5\xb8\x83\xef\xbc\x9a\xe5\xa4\x9a\xe6\xa8\xa1\xe6\x80\x81\xe8\x83\xbd\xe5\x8a\x9b\xe6\x8f\x90\xe5\x8d\x87</title>
    <description>&lt;p&gt;\xe6\x99\xba\xe8\x83\xbd\xe4\xbd\x93 agent \xe6\x88\x90\xe4\xb8\xba\xe7\x84\xa6\xe7\x82\xb9&lt;/p&gt;</description>
    <link>https://example.com/a</link>
    <pubDate>Mon, 06 Oct 2025 08:30:00 +0800</pubDate>
  </item>
  <item>
    <title>\xe4\xbb\x8a\xe5\xa4\xa9\xe5\xa4\xa9\xe6\xb0\x94\xe4\xb8\x8d\xe9\x94\x99</title>
    <description>\xe4\xb8\x8e AI \xe6\x97\xa0\xe5\x85\xb3</description>
    <link>https://example.com/b</link>
    <pubDate>Tue, 07 Oct 2025 09:00:00 +0800</pubDate>
  </item>
</channel></rss>
"""

ATOM_SAMPLE = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Atom Feed</title>
  <entry>
    <title>Open-source LLM inference cost reduction</title>
    <summary>We cut GPU inference cost by 40% with quantization.</summary>
    <link href="https://example.org/x"/>
    <updated>2025-10-05T12:00:00Z</updated>
  </entry>
</feed>
"""


def _article(month: str, dims, source: str = "测试媒体") -> Article:
    """构造一篇测试用文章。"""
    return Article(source=source, category="测试", language="zh",
                   title=f"标题-{month}", published=f"{month}-01", month=month,
                   dimensions=list(dims))


def _paper(month: str, dims) -> Paper:
    """构造一篇测试用论文。"""
    return Paper(title=f"论文-{month}", published=f"{month}-01", month=month,
                 dimension=list(dims))


# ---------------------------------------------------------------- TC35
def test_parse_feed_rss():
    """TC35：RSS 2.0 解析应取出标题、摘要、链接与日期，并剥离 HTML 标签。"""
    articles = parse_feed(RSS_SAMPLE, "测试源", "国内科技媒体", "zh")
    assert len(articles) == 2
    first = articles[0]
    assert "多模态" in first.title
    assert "<p>" not in first.summary          # HTML 标签被剥离
    assert first.link == "https://example.com/a"
    assert first.published == "2025-10-06"
    assert first.month == "2025-10"
    assert "多模态与生成" in first.dimensions
    assert "智能体与自动化" in first.dimensions


# ---------------------------------------------------------------- TC36
def test_parse_feed_atom():
    """TC36：Atom 格式（含 link href 与 updated）也应正确解析。"""
    articles = parse_feed(ATOM_SAMPLE, "Atom源", "国际科技媒体", "en")
    assert len(articles) == 1
    assert articles[0].link == "https://example.org/x"
    assert articles[0].published == "2025-10-05"
    assert "算力与成本" in articles[0].dimensions


# ---------------------------------------------------------------- TC37
def test_parse_feed_坏数据返回空():
    """TC37：非法 XML 或空内容不应抛异常，返回空列表。"""
    assert parse_feed(b"", "空", "类别", "zh") == []
    assert parse_feed(b"<html><body>not a feed</body></html>", "坏", "类别", "zh") == []
    assert parse_feed(b"<rss><channel></channel></rss>", "无条目", "类别", "zh") == []


# ---------------------------------------------------------------- TC38
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Mon, 06 Oct 2025 08:30:00 +0800", "2025-10-06"),
        ("2025-10-06T12:00:00Z", "2025-10-06"),
        ("2025-10-06", "2025-10-06"),
        ("乱七八糟的日期 2025-10-06 出现", "2025-10-06"),
        ("", ""),
        ("完全无法解析", ""),
    ],
)
def test_parse_date_多格式(raw, expected):
    """TC38：多种日期格式都应归一化为 YYYY-MM-DD。"""
    assert _parse_date(raw) == expected


# ---------------------------------------------------------------- TC39
def test_is_topic_relevant():
    """TC39：主题过滤应命中 LLM 相关词，放行无关内容。"""
    assert is_topic_relevant("OpenAI 发布新模型") is True
    assert is_topic_relevant("大模型落地实践") is True
    assert is_topic_relevant("今天吃什么好呢") is False


# ---------------------------------------------------------------- TC40
def test_dimension_stats_占比():
    """TC40：维度占比统计应正确计算百分比并按数量降序。"""
    articles = [
        _article("2025-10", ["智能体与自动化", "算力与成本"]),
        _article("2025-10", ["智能体与自动化"]),
        _article("2025-10", ["多模态与生成"]),
        _article("2025-10", []),
    ]
    stats = dimension_stats(articles)
    mapping = {d: (n, p) for d, n, p in stats}
    assert mapping["智能体与自动化"][0] == 2
    assert mapping["智能体与自动化"][1] == pytest.approx(50.0)
    assert stats[0][0] == "智能体与自动化"
    assert dimension_stats([]) == []


# ---------------------------------------------------------------- TC41
def test_trend_momentum_升温():
    """TC41：近期占比明显更高的方向，动量应为正且排在最前。"""
    articles = []
    for month in ["2025-08", "2025-09", "2025-10"]:        # 近期
        for _ in range(5):
            articles.append(_article(month, ["算力与成本"]))
    for month in ["2025-05", "2025-06", "2025-07"]:        # 早期
        for _ in range(5):
            articles.append(_article(month, ["多模态与生成"]))

    momentum = trend_momentum(articles, recent_months=3)
    assert momentum, "样本足够时应返回动量结果"
    top = momentum[0]
    assert top[0] == "算力与成本"
    assert top[3] > 0
    # 多模态方向应降温
    falling = {r[0]: r[3] for r in momentum}
    assert falling["多模态与生成"] < 0


# ---------------------------------------------------------------- TC42
def test_trend_momentum_样本不足返回空():
    """TC42：样本过少或没有日期时应返回空列表，而不是除零崩溃。"""
    assert trend_momentum([_article("2025-10", ["算力与成本"])]) == []
    assert trend_momentum([]) == []
    undated = [Article(source="s", category="c", language="zh", title="无日期")]
    assert trend_momentum(undated) == []


# ---------------------------------------------------------------- TC43
def test_build_forecast_结构完整():
    """TC43：预测报告应包含全部约定字段，且结论非空。"""
    articles = []
    for month in ["2025-08", "2025-09", "2025-10"]:
        for _ in range(4):
            articles.append(_article(month, ["智能体与自动化"]))
    for month in ["2025-06", "2025-07"]:
        for _ in range(4):
            articles.append(_article(month, ["开源与本地部署"]))

    report = build_forecast(articles)
    for key in ["样本文章数", "覆盖媒体数", "各媒体文章数", "月度分布",
                "维度占比", "升温方向", "降温方向", "趋势预测"]:
        assert key in report
    assert report["样本文章数"] == len(articles)
    assert report["趋势预测"], "结论不应为空"


# ---------------------------------------------------------------- TC44
def test_month_windows_数量与倒序():
    """TC44：月份窗口数量正确、按时间倒序、且区间首尾相接。"""
    end = datetime(2025, 10, 15, 12, 0, 0)
    windows = month_windows(6, end=end)
    assert len(windows) == 6
    labels = [w[0] for w in windows]
    assert labels == sorted(labels, reverse=True)
    assert labels[0] == "2025-10"
    # 相邻窗口不得重叠：后一个窗口的结束时间必须早于前一个窗口的起始时间
    for (_la, start_a, _ea), (_lb, _sb, end_b) in zip(windows, windows[1:]):
        assert end_b < start_a
    # 每个窗口的起止时间必须落在自己的月份标签内
    for label, start, end in windows:
        assert start.strftime("%Y-%m") == label
        assert end.strftime("%Y-%m") == label


# ---------------------------------------------------------------- TC45
def test_dimension_by_month_统计():
    """TC45：逐月维度分布应为 {方向: {月份: 篇数}}。"""
    papers = [
        _paper("2025-09", ["算力与成本"]),
        _paper("2025-09", ["算力与成本", "智能体与自动化"]),
        _paper("2025-10", ["智能体与自动化"]),
    ]
    table = dimension_by_month(papers)
    assert table["算力与成本"]["2025-09"] == 2
    assert table["智能体与自动化"]["2025-10"] == 1
    assert "2025-10" not in table["算力与成本"]


# ---------------------------------------------------------------- TC46
def test_compute_trend_用占比而非绝对数():
    """TC46：动量必须基于占比——两期总量不同但比例相同则动量应约为 0。"""
    papers = []
    for _ in range(10):                      # 近期 10 篇，全部命中
        papers.append(_paper("2025-10", ["算力与成本"]))
    for _ in range(30):                      # 基准 30 篇，同样全部命中
        papers.append(_paper("2025-07", ["算力与成本"]))

    rows = compute_trend(papers, recent_months=1, baseline_months=1, min_sample=10)
    row = next(r for r in rows if r["方向"] == "算力与成本")
    assert row["动量(百分点)"] == pytest.approx(0.0, abs=0.01)
    assert row["近期占比%"] == pytest.approx(100.0)


# ---------------------------------------------------------------- TC47
def test_compute_trend_样本不足():
    """TC47：论文过少时应返回空列表（避免给出不可靠结论）。"""
    assert compute_trend([_paper("2025-10", ["算力与成本"])]) == []


# ---------------------------------------------------------------- TC48
def test_top_keywords_去冗余():
    """TC48：高频技术词应剔除被长词包含的短词。"""
    papers = [_paper("2025-10", []) for _ in range(3)]
    for i, p in enumerate(papers):
        p.title = "大模型推理优化研究" if i < 2 else "大模型应用"
    kws = dict(top_keywords(papers, top_n=20))
    assert "大模型" in kws
    assert "模型" not in kws


# ---------------------------------------------------------------- TC49
def test_build_report_结论可读():
    """TC49：报告结论应是可读文本列表，且包含总量变化描述。"""
    monthly = {"2025-07": 100, "2025-08": 120, "2025-09": 150, "2025-10": 180}
    papers = []
    for month, count in [("2025-09", 5), ("2025-10", 15), ("2025-07", 10), ("2025-08", 10)]:
        for _ in range(count):
            papers.append(_paper(month, ["算力与成本"]))
    report = build_report(monthly, papers)
    assert isinstance(report["结论"], list)
    assert report["结论"]
    text = " ".join(report["结论"])
    assert "80.0%" in text or "+80.0%" in text    # (180-100)/100
    assert report["月度总命中"]["2025-10"] == 180
