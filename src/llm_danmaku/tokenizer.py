# -*- coding: utf-8 -*-
"""中文分词：jieba 精确模式 + 自定义词表 + 停用词过滤。

jieba 词典加载是整条管道最昂贵的一次性开销，因此用 lru_cache 保证
全局只加载一次（性能优化点之一，详见 profile_runner 的输出）。
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Set

import jieba

from . import config

logger = logging.getLogger(__name__)

# 领域词典：权重越高越容易被切出来（保证"大语言模型""多模态"不被切碎）
DOMAIN_WORDS: Dict[str, int] = {
    "大语言模型": 100, "大模型": 100, "语言模型": 90, "多模态": 90,
    "机器学习": 80, "深度学习": 80, "神经网络": 80, "Transformer": 80,
    "ChatGPT": 90, "GPT": 90, "GPT4": 90, "GPT-4": 90, "LLM": 90,
    "OpenAI": 85, "Claude": 85, "Gemini": 85, "文心一言": 85, "通义千问": 85,
    "豆包": 80, "Kimi": 80, "DeepSeek": 90, "深度求索": 85, "智谱": 80,
    "提示词": 85, "智能体": 85, "微调": 80, "预训练": 80, "推理": 80,
    "算力": 80, "显卡": 75, "英伟达": 80, "开源模型": 80, "闭源": 75,
    "幻觉": 80, "对齐": 75, "隐私": 75, "取代": 75, "失业": 75,
    "人工智能": 90, "大厂": 70, "白嫖": 70, "免费版": 70, "订阅费": 70,
    "写代码": 80, "编程": 75, "生产力": 75, "降本增效": 75, "自动化": 70,
    "文生图": 80, "文生视频": 80, "数字人": 75, "自动驾驶": 80,
}

# 只由标点/数字组成的分词结果直接丢弃
_MEANINGLESS = re.compile(r"^[\W_\d]+$", re.UNICODE)
_HAS_CJK = re.compile(r"[\u4e00-\u9fff]")
# 允许保留的纯英文技术词（长度 >= 2）
_KEEP_ASCII = {"ai", "llm", "gpt", "api", "gpu", "cpu", "nlp", "agi", "sdk",
               "ai绘画", "prompt", "chatgpt", "openai", "claude", "gemini",
               "deepseek", "kimi", "copilot", "cursor", "transformer", "rag"}


@lru_cache(maxsize=1)
def load_stopwords(path: str | None = None) -> Set[str]:
    """加载停用词表（进程内只读一次）。

    除 config.STOPWORDS 内置集合外，还会读取 resources/stopwords.txt，
    这样词表可以在不改代码的情况下扩充（作业要求的可维护性）。

    Returns:
        小写化的停用词集合。
    """
    words: Set[str] = {w.lower() for w in config.STOPWORDS}
    target = Path(path) if path else (config.RESOURCE_DIR / "stopwords.txt")
    if target.exists():
        try:
            with open(target, "r", encoding="utf-8") as fh:
                for line in fh:
                    word = line.strip()
                    if word and not word.startswith("#"):
                        words.add(word.lower())
            logger.info("停用词表加载完成：%d 个（含 %s）", len(words), target.name)
        except OSError as exc:
            logger.warning("停用词表读取失败 %s：%s", target, exc)
    else:
        logger.warning("未找到停用词表 %s，仅使用内置停用词", target)
    return words


@lru_cache(maxsize=1)
def _prepare() -> bool:
    """加载自定义词典（进程内只执行一次）。"""
    for word, weight in DOMAIN_WORDS.items():
        jieba.add_word(word, freq=weight)
    jieba.initialize()
    load_stopwords()
    logger.info("jieba 词典加载完成，自定义词 %d 个", len(DOMAIN_WORDS))
    return True


def load_user_dict(path: str) -> int:
    """从外部文件加载自定义词典（每行一个词）。

    Returns:
        成功加载的词数。
    """
    count = 0
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                word = line.strip()
                if word and not word.startswith("#"):
                    jieba.add_word(word, freq=80)
                    DOMAIN_WORDS[word] = 80
                    count += 1
    except OSError as exc:
        logger.warning("自定义词典加载失败 %s: %s", path, exc)
    return count


def is_meaningful(word: str) -> bool:
    """判断一个词是否值得进入词频统计。

    规则：长度足够、非纯符号数字、不在停用词表；中文词至少 2 字，
    英文/技术缩写按白名单保留。
    """
    w = word.strip()
    if not w or len(w) < config.MIN_WORD_LEN:
        return False
    if _MEANINGLESS.match(w):
        return False
    if w.lower() in load_stopwords():
        return False
    if _HAS_CJK.search(w):
        return True
    return w.lower() in _KEEP_ASCII


def tokenize(text: str) -> List[str]:
    """单条文本分词。

    Examples:
        >>> "大模型" in tokenize("大模型的幻觉问题很严重")
        True
    """
    _prepare()
    return [w for w in jieba.lcut(text, cut_all=False) if is_meaningful(w)]


def tokenize_batch(texts: Iterable[str]) -> List[str]:
    """批量分词并展平为一个词列表（词频统计的直接输入）。"""
    _prepare()
    words: List[str] = []
    for text in texts:
        words.extend(w for w in jieba.lcut(text, cut_all=False) if is_meaningful(w))
    return words


def drop_substring_words(
    counts: Dict[str, int],
    min_longer_count: int = 3,
) -> Dict[str, int]:
    """剔除被更高频长词包含的短词，避免词云出现重复语义。

    典型问题：切出「大模型」和「模型」两个词，词云里同时出现，
    视觉上重复且误导。规则：若存在更长的词包含当前词，且该长词词频
    不低于 min_longer_count，则认为当前词是冗余子串，予以剔除。

    Args:
        counts: {词: 词频} 字典。
        min_longer_count: 长词被判为"确实有意义"的最低词频。

    Returns:
        过滤后的字典（不修改入参）。

    Examples:
        >>> drop_substring_words({"大模型": 50, "模型": 40, "型": 9})
        {'大模型': 50}
    """
    words = list(counts)
    redundant = set()
    for word in words:
        if word in redundant:
            continue
        for other in words:
            if other == word or len(other) <= len(word) or word not in other:
                continue
            if counts.get(other, 0) >= min_longer_count:
                redundant.add(word)
                break
    return {w: c for w, c in counts.items() if w not in redundant}
