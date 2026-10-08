# -*- coding: utf-8 -*-
"""项目全局配置。

所有可调参数集中在此，便于测试与复现；其他模块只读取本模块常量，
不硬编码路径与魔法数字（低耦合要求）。
"""
from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
# 本文件位于 <项目根>/src/llm_danmaku/config.py。
# 若以 `pip install -e .` 方式安装，__file__ 仍指向 src 目录，因此这里
# 需要自动判断"项目根"：向上找到同时包含 requirements.txt 与 src 目录的那一层。
_HERE = Path(__file__).resolve()


def _detect_project_root(start: Path) -> Path:
    """向上查找项目根目录（含 requirements.txt 的那一层）。"""
    for candidate in [start, *start.parents]:
        if (candidate / "requirements.txt").exists() and (candidate / "src").is_dir():
            return candidate
    return start.parent.parent          # 兜底：<根>/src/llm_danmaku -> <根>


PROJECT_ROOT = _detect_project_root(_HERE.parent)
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"            # 每个视频的原始弹幕 JSONL（断点续爬用）
OUTPUT_DIR = PROJECT_ROOT / "output"  # Excel / 图片 / HTML 产物
LOG_DIR = PROJECT_ROOT / "logs"
RESOURCE_DIR = PROJECT_ROOT / "resources"

VIDEO_LIST_FILE = DATA_DIR / "videos.json"
CLEANED_FILE = DATA_DIR / "danmaku_cleaned.jsonl"
STATS_FILE = OUTPUT_DIR / "danmaku_stats.json"
EXCEL_FILE = OUTPUT_DIR / "danmaku_analysis.xlsx"
WORDCLOUD_FILE = OUTPUT_DIR / "wordcloud.png"
CHART_DIR = OUTPUT_DIR / "charts"
DASHBOARD_FILE = OUTPUT_DIR / "dashboard.html"

for _d in (DATA_DIR, RAW_DIR, OUTPUT_DIR, LOG_DIR, CHART_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------------
# 爬取参数
# --------------------------------------------------------------------------
KEYWORDS = ["大语言模型", "大模型", "LLM"]
SEARCH_ORDER = "totalrank"          # 综合排序
PAGE_SIZE = 50                      # B 站搜索接口单页上限
TARGET_VIDEO_COUNT = 300            # 作业要求：综合排序前 300 的视频弹幕

REQUEST_TIMEOUT = 20                # 单次请求超时（秒）
REQUEST_DELAY = (0.35, 0.75)        # 每次请求间随机休眠区间（秒），防触发风控
MAX_RETRIES = 4                     # 单请求最大重试次数
BACKOFF_BASE = 1.5                  # 指数退避基数：sleep = BACKOFF_BASE ** attempt
CRAWL_WORKERS = 6                   # 弹幕并发抓取线程数

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
# B 站部分视频在未登录时只返回部分弹幕；如需完整数据，
# 可设置环境变量 BILI_SESSDATA（浏览器 F12 里复制 Cookie 中的 SESSDATA 值）。
SESSDATA = os.getenv("BILI_SESSDATA", "").strip()

# --------------------------------------------------------------------------
# 清洗与分词参数
# --------------------------------------------------------------------------
MIN_DANMAKU_LEN = 2                 # 少于此长度的弹幕视为灌水
MAX_DANMAKU_LEN = 100               # 超长弹幕多为复制粘贴的歌词/小作文
TOP_COMMENT_N = 8                   # 作业要求：输出数量排名前 8 的弹幕
TOP_WORD_N = 100                    # 词频榜长度（词云与图表取前 N）
MIN_WORD_LEN = 2                    # 词云中至少 2 个汉字，避免大量单字噪声

# 高价值讨论词表：用于筛选"与 LLM 应用相关"的弹幕（结论与 Top8 的依据）
LLM_TOPIC_WORDS = [
    "大模型", "大语言模型", "语言模型", "LLM", "GPT", "ChatGPT", "AI",
    "人工智能", "智能体", "Agent", "多模态", "提示词", "Prompt", "微调",
    "训练", "推理", "算力", "芯片", "显卡", "开源", "闭源", "国产",
    "幻觉", "胡说", "编造", "对齐", "安全", "隐私", "取代", "失业",
    "编程", "写代码", "写作", "绘画", "论文", "科研", "教育", "医疗",
    "客服", "翻译", "办公", "搜索", "剪辑", "游戏", "自动驾驶",
    "收费", "免费", "成本", "价格", "订阅", "会员", "额度", "白嫖",
]

# 单字/虚词停用词（保留少量有判断力的单字由 MIN_WORD_LEN 控制）
STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都", "一", "一个",
    "上", "也", "很", "到", "说", "要", "去", "你", "会", "着", "没有", "看", "好",
    "自己", "这", "那", "这个", "那个", "什么", "怎么", "为什么", "可以", "还是",
    "但是", "因为", "所以", "如果", "已经", "现在", "时候", "真的", "感觉", "觉得",
    "知道", "这个", "我们", "他们", "你们", "东西", "问题", "事情", "地方", "样子",
    "up", "UP", "up主", "视频", "弹幕", "哈哈", "哈哈哈", "哈哈哈哈", "笑死", "emmm",
    "确实", "其实", "应该", "可能", "不是", "就是", "这样", "那样", "然后", "而且",
    "一下", "一点", "有点", "太", "更", "最", "挺", "蛮", "超", "真", "啊", "吧",
    "呢", "吗", "呀", "哦", "嗯", "唉", "哇", "喂", "诶", "em", "emm",
}

# --------------------------------------------------------------------------
# 可视化参数
# --------------------------------------------------------------------------
# 词云中文字体（Windows 自带微软雅黑；缺失时回退到黑体/宋体）
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]
FONT_PATH = next((f for f in _FONT_CANDIDATES if Path(f).exists()), None)

WORDCLOUD_SIZE = (1600, 900)
WORDCLOUD_MAX_WORDS = 300
WORDCLOUD_BG = "#0f1420"            # 深色底显得更"大屏"
WORDCLOUD_COLORMAP = "viridis"

CHART_DPI = 150
CHART_STYLE = "seaborn-v0_8-darkgrid"

# --------------------------------------------------------------------------
# 性能采样
# --------------------------------------------------------------------------
PROFILE_DIR = OUTPUT_DIR / "profile"
PROFILE_DIR.mkdir(parents=True, exist_ok=True)
