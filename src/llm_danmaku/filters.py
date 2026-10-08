# -*- coding: utf-8 -*-
"""弹幕噪声识别规则。

作业要求"过滤掉类似 666、点赞等的噪声信息"，本模块把判断逻辑集中管理，
便于单测覆盖各种边界，也便于后续按需扩充词表。
"""
from __future__ import annotations

import re
from typing import Iterable, List

# 纯数字 / 纯符号 / 纯空白
_ONLY_DIGITS = re.compile(r"^[\d\s]+$")
_ONLY_SYMBOLS = re.compile(r"^[^\w\u4e00-\u9fff]+$")
# 单个字符重复 3 次以上：哈哈哈、666、啊啊啊、www
_REPEAT_CHAR = re.compile(r"^(.)\1{2,}$")
# 复读机式刷屏：同一短串整体重复
# 复读机式刷屏：短串整体重复，如「大模型大模型大模型」「6啊6啊6啊」。
# 必须限制重复单元长度（>=2）与总长度（<=24），否则「大」*100 这种正常长弹幕
# 会被 unit="大" 整体匹配而误杀。
_REPEAT_TOKEN = re.compile(r"^(.{2,6}?)\1{2,}$")
# @某人、URL、话题标签
_AT_SOMEONE = re.compile(r"@[\w\u4e00-\u9fff\-]{1,20}")
_URL = re.compile(r"(https?://\S+|www\.\S+|\S+\.(com|cn|net|org)\b)")
# 弹幕常见的"打卡"模板
_CHECK_IN = re.compile(r"^\d{4}[-/年]?\d{0,2}[-/月]?\d{0,2}日?\s*(打卡|签到|报道|报到|前来观看)")

# 精确匹配的黑名单（小写比较）
NOISE_EXACT = {
    "666", "6666", "66666", "666666", "233", "2333", "23333", "hhh", "hhhh",
    "哈哈哈", "哈哈哈哈", "哈哈哈哈哈", "笑死", "笑死我了", "awsl", "xswl",
    "前排", "板凳", "沙发", "打卡", "签到", "来了", "我来了", "来啦", "报道",
    "报到", "围观", "路过", "顶", "支持", "顶一个", "赞", "点赞", "好评",
    "nb", "np", "tql", "yyds", "orz", "emmm", "emm", "em", "嗯", "哦", "啊",
    "1", "2", "3", "第一", "第二", "空白", "空降", "空降成功", "空降兵",
    "下次一定", "一键三连", "素质三连", "催更", "更新", "更了", "催更了",
    "火钳刘明", "已阅", "留名", "收藏", "关注了", "已关注", "投币", "三连",
    "有生之年", "考古", "考古现场", "爷青回", "泪目", "破防", "绝了", "牛",
    "厉害", "太强了", "强", "好活", "干活", "加油", "冲冲冲", "干杯",
}

# 子串黑名单：命中即视为噪声（用于"前排打卡"这类组合词）
NOISE_SUBSTRINGS = ("一键三连", "素质三连", "火钳刘明", "爷青回", "下次一定",
                    "空降成功", "前排围观", "考古现场", "催更")


def normalize(text: str) -> str:
    """去首尾空白、合并连续空白、全角转半角（数字与字母）。"""
    if not text:
        return ""
    cleaned = re.sub(r"\s+", " ", text.strip())
    # 全角字符 -> 半角（数字、字母、常见符号）
    out_chars: List[str] = []
    for ch in cleaned:
        code = ord(ch)
        if code == 0x3000:
            out_chars.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:
            out_chars.append(chr(code - 0xFEE0))
        else:
            out_chars.append(ch)
    return "".join(out_chars).strip()


def strip_mentions(text: str) -> str:
    """去掉 @某人、URL 与话题标签，保留正文（并合并因此产生的多余空格）。"""
    text = _AT_SOMEONE.sub("", text)
    text = _URL.sub("", text)
    text = re.sub(r"#([^#]{1,20})#", r"\1", text)  # #话题# 保留话题文字
    return re.sub(r"\s{2,}", " ", text).strip()


def is_noise(text: str, min_len: int = 2, max_len: int = 100) -> bool:
    """判断一条弹幕是否为噪声。

    Args:
        text: 原始弹幕文本。
        min_len: 最小保留长度（含），短于此视为灌水。
        max_len: 最大保留长度（含），超长视为复制粘贴。

    Returns:
        True 表示应被过滤。

    Examples:
        >>> is_noise("666")
        True
        >>> is_noise("大模型会不会取代程序员")
        False
    """
    s = normalize(text)
    if not s:
        return True
    if len(s) < min_len or len(s) > max_len:
        return True
    if _ONLY_DIGITS.match(s) or _ONLY_SYMBOLS.match(s):
        return True
    # 单字符重复（哈哈哈）与短串整体重复（大模型大模型大模型）都算刷屏
    if len(s) <= 24 and (_REPEAT_CHAR.match(s) or _REPEAT_TOKEN.match(s)):
        return True
    if _CHECK_IN.match(s):
        return True

    lowered = s.lower()
    if lowered in NOISE_EXACT:
        return True
    if any(sub in s for sub in NOISE_SUBSTRINGS):
        return True
    # 去掉标点后只剩黑名单词，如 "666！" "哈哈哈哈哈~"
    stripped = re.sub(r"[^\w\u4e00-\u9fff]", "", lowered)
    if stripped and stripped in NOISE_EXACT:
        return True
    return False


def noise_ratio(texts: Iterable[str]) -> float:
    """统计一批弹幕的噪声占比，用于数据质量报告。"""
    items = list(texts)
    if not items:
        return 0.0
    return sum(1 for t in items if is_noise(t)) / len(items)
