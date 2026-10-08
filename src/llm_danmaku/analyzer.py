# -*- coding: utf-8 -*-
"""统计分析：每类弹幕总量、Top-N 弹幕、词频、领域分布与结论依据。

"每类弹幕"在作业里有两种合理解读，本模块两者都算，避免歧义：
  1. **按来源视频分类**：每个视频（即每类弹幕）贡献的弹幕总量与高频弹幕；
  2. **按主题分类**：把弹幕归入应用领域 / 成本 / 风险等类别，统计各类占比。

性能设计：一次性遍历完成多路统计（Counter 为 C 实现），避免对同一批数据
反复扫描；分词结果复用，不重复调用 jieba。
"""
from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import config
from .cleaner import CleanReport, clean_batch
from .danmaku_crawler import CrawlResult, Danmaku
from .tokenizer import drop_substring_words, tokenize_batch

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 主题词典：用于"数据结论"部分提供可解释的判断依据
# --------------------------------------------------------------------------
DOMAIN_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "编程开发": ("编程", "写代码", "代码", "程序员", "debug", "copilot", "cursor",
                 "ide", "重构", "算法题", "接口", "前端", "后端"),
    "写作办公": ("写作", "写文章", "文案", "ppt", "excel", "word", "周报",
                 "邮件", "办公", "总结", "翻译", "文档"),
    "绘画设计": ("绘画", "画图", "文生图", "sdxl", "midjourney", "stable diffusion",
                 "设计", "ps", "出图", "渲染"),
    "视频音频": ("视频", "剪辑", "配音", "文生视频", "sora", "数字人", "语音",
                 "音乐", "音频", "ai歌手"),
    "科研学术": ("论文", "科研", "文献", "综述", "实验", "学术", "sci",
                 "读博", "课题", "开题"),
    "教育学习": ("教育", "学习", "老师", "学生", "作业", "考试", "英语",
                 "辅导", "刷题", "教学", "网课"),
    "客服营销": ("客服", "销售", "营销", "客服机器人", "话术", "带货", "运营"),
    "医疗健康": ("医疗", "医生", "诊断", "病历", "健康", "问诊", "药物"),
    "驾驶出行": ("自动驾驶", "智驾", "辅助驾驶", "特斯拉", "车机", "导航"),
    "游戏娱乐": ("游戏", "npc", "陪玩", "剪辑", "直播", "娱乐", "虚拟主播"),
    "数据搜索": ("搜索", "检索", "数据分析", "报表", "爬虫", "bi", "数据库"),
}

COST_KEYWORDS = ("收费", "免费", "价格", "成本", "贵", "便宜", "订阅", "会员",
                 "额度", "白嫖", "付费", "氪金", "token", "算力", "显卡", "电费")

RISK_KEYWORDS = ("幻觉", "胡说", "编造", "错误", "不准确", "取代", "失业",
                 "焦虑", "隐私", "泄露", "抄袭", "作弊", "版权", "伦理",
                 "失控", "安全", "依赖", "退化", "垃圾")

BENEFIT_KEYWORDS = ("效率", "提效", "方便", "好用", "强大", "厉害", "生产力",
                    "降本", "省时间", "真香", "神器", "牛逼", "nb", "有用",
                    "帮助", "进步", "普惠")

POSITIVE_WORDS = ("好", "强", "牛", "赞", "喜欢", "厉害", "有用", "方便", "效率",
                  "神器", "真香", "期待", "支持", "优秀", "惊艳", "震撼", "爱了")
NEGATIVE_WORDS = ("差", "垃圾", "慢", "贵", "假", "错", "坑", "失望", "担心",
                  "害怕", "焦虑", "失业", "取代", "胡说", "幻觉", "没用", "鸡肋")


@dataclass
class VideoStat:
    """单个视频（"每类弹幕"之一）的统计结果。"""

    rank: int
    bvid: str
    title: str
    author: str = ""
    play: int = 0
    raw_count: int = 0          # 抓到的原始弹幕条数
    cleaned_count: int = 0      # 清洗后条数
    unique_count: int = 0       # 去重后条数
    top_comments: List[Tuple[str, int]] = field(default_factory=list)


@dataclass
class DanmakuStats:
    """全量统计结果，是 Excel 导出与可视化的唯一数据来源。"""

    total_raw: int = 0
    total_cleaned: int = 0
    total_unique: int = 0
    video_count: int = 0
    failed_videos: int = 0
    clean_report: Dict[str, object] = field(default_factory=dict)
    top_comments: List[Tuple[str, int]] = field(default_factory=list)
    top_llm_comments: List[Tuple[str, int]] = field(default_factory=list)
    top_words: List[Tuple[str, int]] = field(default_factory=list)
    repeated_comments: List[Tuple[str, int]] = field(default_factory=list)
    video_stats: List[VideoStat] = field(default_factory=list)
    domain_counts: Dict[str, int] = field(default_factory=dict)
    cost_count: int = 0
    risk_count: int = 0
    benefit_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    progress_buckets: List[Tuple[str, int]] = field(default_factory=list)
    generated_at: str = ""

    def as_dict(self) -> Dict[str, object]:
        """转为可 JSON 序列化的字典（tuple 统一转 list）。"""
        data = asdict(self)
        return data

    def save(self, path: Optional[Path] = None) -> Path:
        """把统计结果写入 JSON，供可视化与博客引用。

        注意：默认参数不能写成 ``path=config.STATS_FILE``——Python 的默认值在
        **函数定义时**求值，那样会把路径固定死，导致运行期修改配置不生效
        （也让单元测试无法重定向输出目录）。因此这里用 None 哨兵，在函数体内取。
        """
        target = Path(path) if path is not None else config.STATS_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(self.as_dict(), fh, ensure_ascii=False, indent=2)
        logger.info("统计结果已写入 %s", target)
        return target

    # ------------------------------------------------------------ 派生指标
    @property
    def positive_ratio(self) -> float:
        """正面弹幕占比。"""
        total = self.positive_count + self.negative_count + self.neutral_count
        return self.positive_count / total if total else 0.0

    def domain_top(self, n: int = 8) -> List[Tuple[str, int]]:
        """应用领域 Top-N。"""
        return sorted(self.domain_counts.items(), key=lambda kv: kv[1], reverse=True)[:n]


def classify_domain(text: str) -> Optional[str]:
    """把一个弹幕归入应用领域；未命中返回 None。

    Examples:
        >>> classify_domain("用GPT写代码效率翻倍")
        '编程开发'
    """
    lowered = text.lower()
    for domain, words in DOMAIN_KEYWORDS.items():
        if any(w in lowered for w in words):
            return domain
    return None


def count_hits(texts: Sequence[str], keywords: Sequence[str]) -> int:
    """统计命中任一关键词的弹幕条数。"""
    lowered = (t.lower() for t in texts)
    keys = tuple(k.lower() for k in keywords)
    return sum(1 for t in lowered if any(k in t for k in keys))


def _progress_bucket(progress_ms: int, duration_ms: int = 0) -> str:
    """把弹幕时间点映射到占视频总长 1/10 的区间标签。

    优先使用**该视频的真实总时长**做百分比换算（多分P 视频的 progress
    已由爬虫按分P 时长做了偏移归一化，两者配合才能得到正确的相对位置）。
    若时长未知，则退化为以 60 分钟为基准、每 6 分钟一个桶的固定分桶。

    Examples:
        >>> _progress_bucket(0)
        '0-10%'
        >>> _progress_bucket(30_000, duration_ms=300_000)
        '10-20%'
    """
    try:
        progress = max(0.0, float(progress_ms))
        duration = float(duration_ms or 0)
    except (TypeError, ValueError):
        return "0-10%"

    if duration > 0:
        ratio = min(0.999999, progress / duration)
        bucket = int(ratio * 10)
    else:
        bucket = min(9, int(progress / 1000 // 360))      # 每桶 360 秒 = 6 分钟
    low = bucket * 10
    return f"{low}-{low + 10}%"


def analyze(
    results: Iterable[CrawlResult],
    cleaned_texts: Sequence[str],
    report: Optional[CleanReport] = None,
    top_comment_n: int = config.TOP_COMMENT_N,
    top_word_n: int = config.TOP_WORD_N,
) -> DanmakuStats:
    """对抓取结果与清洗结果做全量统计。

    **关于去重口径（关键设计决策）**
    本项目采用「**视频内去重、跨视频计数**」：
      - 同一个视频里重复刷屏的弹幕只算一次（避免一个复读机用户污染词频）；
      - 但同一条弹幕出现在 N 个不同视频里要计 N 次。

    为什么不能做全局去重：弹幕是高度长尾的自由文本，跨视频几乎不重复。
    早期版本做了全局去重，结果 Top8 弹幕全部是"出现 1 次"，排名失去意义。
    改成跨视频计数后，「幻觉就是这样产生的」这类被大量视频反复提及的观点
    才会自然浮到榜首，这才是作业要的"数量排名前 8 的弹幕"。

    Args:
        results: 每个视频的抓取结果。
        cleaned_texts: 全量清洗后的弹幕文本（用于词频与主题统计）。
        report: 清洗报告（可选，用于填充数据质量指标）。
        top_comment_n: Top 弹幕取前几名（作业要求 8）。
        top_word_n: 词频榜长度。

    Returns:
        DanmakuStats 实例。
    """
    import datetime

    result_list = list(results)
    stats = DanmakuStats(
        video_count=len(result_list),
        failed_videos=sum(1 for r in result_list if not r.ok),
        generated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # ---------- 1. 每个视频的"每类弹幕"统计 ----------
    # 视频内去重，但把去重前的条数单独记下来（用于数据质量说明）
    video_stats: List[VideoStat] = []
    global_counter: Counter = Counter()          # 跨视频累计（视频内已去重）
    global_llm_counter: Counter = Counter()
    llm_words = tuple(w.lower() for w in config.LLM_TOPIC_WORDS)
    all_cleaned: List[str] = []                  # 视频内去重后的全部弹幕

    for idx, res in enumerate(result_list, start=1):
        raw_texts = [dm.text for dm in res.danmaku]
        kept, _ = clean_batch(raw_texts, deduplicate=True)
        counter = Counter(kept)

        video_stats.append(
            VideoStat(
                rank=idx, bvid=res.bvid, title=res.title,
                raw_count=len(raw_texts),        # 该视频抓到的原始弹幕数
                cleaned_count=sum(counter.values()),
                unique_count=len(counter),
                top_comments=counter.most_common(top_comment_n),
            )
        )
        stats.total_raw += len(raw_texts)

        # 跨视频累计：每条弹幕在每个视频里只贡献一次
        global_counter.update(counter.keys())
        all_cleaned.extend(counter.keys())
        global_llm_counter.update(
            t for t in counter if any(w in t.lower() for w in llm_words)
        )

    stats.video_stats = video_stats

    # ---------- 2. 全局 Top 弹幕（跨视频出现次数） ----------
    stats.total_cleaned = len(all_cleaned)
    stats.total_unique = len(global_counter)
    stats.top_comments = global_counter.most_common(top_comment_n)
    stats.top_llm_comments = global_llm_counter.most_common(top_comment_n)

    # ---------- 3. 复读机式刷屏（视频内的高频重复） ----------
    repeated: Counter = Counter()
    for res in result_list:
        counter = Counter(dm.text.strip() for dm in res.danmaku if dm.text.strip())
        for text, count in counter.items():
            if count > 1:
                repeated[text] = max(repeated[text], count)
    stats.repeated_comments = repeated.most_common(15)

    # ---------- 4. 领域 / 成本 / 风险 / 情感（多路一次遍历） ----------
    domain_counts: Dict[str, int] = defaultdict(int)
    pos = neg = neu = 0
    domain_items = [(d, tuple(w.lower() for w in ws)) for d, ws in DOMAIN_KEYWORDS.items()]
    cost_keys = tuple(k.lower() for k in COST_KEYWORDS)
    risk_keys = tuple(k.lower() for k in RISK_KEYWORDS)
    benefit_keys = tuple(k.lower() for k in BENEFIT_KEYWORDS)

    for text in all_cleaned:
        lowered = text.lower()
        for domain, words in domain_items:
            if any(w in lowered for w in words):
                domain_counts[domain] += 1
                break                      # 一条弹幕只归一个主领域，避免重复计数
        if any(k in lowered for k in cost_keys):
            stats.cost_count += 1
        if any(k in lowered for k in risk_keys):
            stats.risk_count += 1
        if any(k in lowered for k in benefit_keys):
            stats.benefit_count += 1
        is_pos = any(w in text for w in POSITIVE_WORDS)
        is_neg = any(w in text for w in NEGATIVE_WORDS)
        if is_pos and not is_neg:
            pos += 1
        elif is_neg and not is_pos:
            neg += 1
        else:
            neu += 1

    stats.domain_counts = dict(domain_counts)
    stats.positive_count, stats.negative_count, stats.neutral_count = pos, neg, neu

    # ---------- 5. 弹幕在视频时间轴上的分布 ----------
    progress_counter: Counter = Counter()
    for res in result_list:
        for dm in res.danmaku:
            progress_counter[_progress_bucket(dm.progress, res.duration_ms)] += 1
    stats.progress_buckets = sorted(
        progress_counter.items(), key=lambda kv: int(kv[0].split("-")[0])
    )

    # ---------- 6. 词频（分词一次性完成） ----------
    # 先按子串去重（"大模型"/"模型" 只保留更有信息量的那个），再取 Top-N，
    # 避免词云与词频榜出现语义重复的条目。
    words = tokenize_batch(all_cleaned)
    word_counts = drop_substring_words(dict(Counter(words)))
    stats.top_words = Counter(word_counts).most_common(top_word_n)

    if report is not None:
        stats.clean_report = report.as_dict()

    logger.info(
        "统计完成：视频 %d 个，原始弹幕 %d 条，视频内去重后 %d 条，"
        "跨视频唯一文本 %d 种，词汇 %d 个",
        stats.video_count, stats.total_raw, stats.total_cleaned,
        stats.total_unique, len(words),
    )
    return stats
