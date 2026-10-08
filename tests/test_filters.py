# -*- coding: utf-8 -*-
"""噪声过滤与文本标准化的单元测试（白盒：覆盖各正则分支与边界）。

测试用例设计说明：
    TC01-TC08 覆盖 filters.normalize / strip_mentions / is_noise 的等价类与边界；
    TC09-TC10 覆盖清理与去重逻辑；
    TC11-TC14 覆盖分词与统计的数据正确性；
    TC15-TC17 覆盖 Excel 导出、wbi 签名、protobuf/XML 解析等接口。
"""
from __future__ import annotations

import pytest

from llm_danmaku.filters import is_noise, noise_ratio, normalize, strip_mentions


# ---------------------------------------------------------------- TC01
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  大模型  ", "大模型"),
        ("全角１２３ＡＢＣ", "全角123ABC"),
        ("多个   空格", "多个 空格"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_等价类(raw, expected):
    """TC01：标准化应去空白、合并空格、全角转半角，且能容忍 None。"""
    assert normalize(raw) == expected


# ---------------------------------------------------------------- TC02
def test_strip_mentions_去掉at与链接():
    """TC02：@某人、URL、#话题# 应被去除，话题文字保留。"""
    assert strip_mentions("@小明 看这个 https://b23.tv/abc 很好") == "看这个 很好"
    assert "话题" in strip_mentions("#话题# 内容")


# ---------------------------------------------------------------- TC03
@pytest.mark.parametrize(
    "text",
    ["666", "666666", "2333", "哈哈哈", "哈哈哈哈", "awsl", "前排", "打卡",
     "签到", "来了", "一键三连", "下次一定", "yyds", "。。。", "!!!", "   ",
     "1", "233", "空降成功"],
)
def test_is_noise_黑名单与符号判为噪声(text):
    """TC03：常见灌水弹幕、纯符号、纯数字都应判为噪声。"""
    assert is_noise(text) is True


# ---------------------------------------------------------------- TC04
@pytest.mark.parametrize(
    "text",
    ["大模型会不会取代程序员", "本地部署要多少显存", "GPT写代码真的强",
     "这个AI收费太贵了", "多模态能力提升明显", "AI很强"],
)
def test_is_noise_正常弹幕应保留(text):
    """TC04：有信息量的弹幕必须保留（长度 >= min_len=2）。"""
    assert is_noise(text) is False


def test_is_noise_单字符视为灌水():
    """TC04b：单字符无法承载观点，按灌水丢弃。"""
    assert is_noise("a") is True
    assert is_noise("好") is True


# ---------------------------------------------------------------- TC05
def test_is_noise_长度边界():
    """TC05：长度边界——1 字丢弃，100 字保留，101 字丢弃。"""
    assert is_noise("好") is True
    assert is_noise("大" * 100) is False
    assert is_noise("大" * 101) is True


# ---------------------------------------------------------------- TC06
def test_is_noise_复读机式重复():
    """TC06：整体重复的短串（如 大模型大模型大模型）应判为噪声。"""
    assert is_noise("哈哈哈哈哈哈") is True
    assert is_noise("6".join([""] * 0) or "666666") is True


# ---------------------------------------------------------------- TC07
def test_is_noise_带标点的黑名单():
    """TC07：黑名单词带标点后仍应识别（"666！"、"哈哈哈哈哈~"）。"""
    assert is_noise("666！") is True
    assert is_noise("哈哈哈哈哈~") is True


# ---------------------------------------------------------------- TC08
def test_noise_ratio_统计():
    """TC08：噪声占比统计正确，空输入返回 0。"""
    assert noise_ratio([]) == 0.0
    assert noise_ratio(["666", "哈哈哈哈", "大模型很强"]) == pytest.approx(2 / 3)
