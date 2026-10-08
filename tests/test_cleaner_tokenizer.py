# -*- coding: utf-8 -*-
"""清洗与分词模块的单元测试。"""
from __future__ import annotations

import pytest

from llm_danmaku.cleaner import clean_batch, clean_text
from llm_danmaku.tokenizer import (
    drop_substring_words,
    is_meaningful,
    load_stopwords,
    tokenize,
    tokenize_batch,
)


# ---------------------------------------------------------------- TC09
def test_clean_text_单条清洗():
    """TC09：清洗应去噪并标准化；噪声返回空串。"""
    assert clean_text("大模型很强") == "大模型很强"
    assert clean_text("666") == ""
    assert clean_text("") == ""
    assert clean_text("@某人 大模型很强") == "大模型很强"


# ---------------------------------------------------------------- TC10
def test_clean_batch_去重与报告():
    """TC10：重复弹幕只保留一次，报告中的计数必须自洽。"""
    raw = ["大模型很强", "大模型很强", "666", "", "   ", "GPT好用"]
    kept, report = clean_batch(raw)
    assert kept == ["大模型很强", "GPT好用"]
    assert report.total == 6
    assert report.kept == 2
    assert report.dropped_duplicate == 1
    assert report.dropped_empty >= 2
    assert 0 < report.keep_ratio < 1
    assert report.as_dict()["原始弹幕数"] == 6


# ---------------------------------------------------------------- TC11
def test_clean_batch_可关闭去重():
    """TC11：deduplicate=False 时重复弹幕应全部保留（用于"每类弹幕总量"统计）。"""
    kept, report = clean_batch(["大模型", "大模型"], deduplicate=False)
    assert kept == ["大模型", "大模型"]
    assert report.dropped_duplicate == 0


# ---------------------------------------------------------------- TC12
def test_tokenize_领域词不被切碎():
    """TC12：自定义词典必须让"大语言模型""多模态"作为整词出现。"""
    words = tokenize("大语言模型的多模态能力很强")
    assert "大语言模型" in words
    assert "多模态" in words


# ---------------------------------------------------------------- TC13
@pytest.mark.parametrize(
    "word, expected",
    [
        ("大模型", True),
        ("的", False),          # 停用词
        ("a", False),           # 单字符
        ("123", False),         # 纯数字
        ("。。。", False),       # 纯符号
        ("llm", True),          # 技术白名单
        ("GPT", True),
        ("", False),
    ],
)
def test_is_meaningful_规则(word, expected):
    """TC13：词是否入统计的判定规则（停用词/长度/纯符号/白名单）。"""
    assert is_meaningful(word) is expected


# ---------------------------------------------------------------- TC14
def test_tokenize_batch_展平():
    """TC14：批量分词结果长度应大于单条，且停用词不出现。"""
    words = tokenize_batch(["大模型很强大", "用GPT写代码"])
    assert len(words) >= 4
    assert "的" not in words


# ---------------------------------------------------------------- TC14b
def test_stopwords_从文件加载():
    """TC14b：resources/stopwords.txt 必须被真正加载（而不是只放着不用）。"""
    stopwords = load_stopwords()
    assert "的" in stopwords
    assert "视频" in stopwords
    assert len(stopwords) > 50
    # 词表里的词必须被判为无意义
    assert is_meaningful("视频") is False
    assert is_meaningful("大家") is False


# ---------------------------------------------------------------- TC14c
def test_drop_substring_words_去冗余():
    """TC14c：被高频长词包含的短词应被剔除（避免词云语义重复）。"""
    kept = drop_substring_words({"大模型": 50, "模型": 40, "型": 9, "算力": 12})
    assert "大模型" in kept
    assert "模型" not in kept
    assert "型" not in kept
    assert "算力" in kept            # 没有更长的词包含它，应保留

    # 长词本身频次太低时不做剔除（避免误杀）
    low = drop_substring_words({"大模型": 1, "模型": 40}, min_longer_count=3)
    assert "模型" in low
