# -*- coding: utf-8 -*-
"""B 站视频检索：关键词 -> 综合排序 -> 去重取前 N 个视频。

对应作业 2.1 的"综合排序前 300 的视频"。
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Dict, Iterable, List, Optional

from . import config
from .bili_client import API_SEARCH, BiliClient

logger = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")


@dataclass
class VideoInfo:
    """一个视频的元信息。"""

    bvid: str
    title: str
    author: str = ""
    mid: int = 0
    play: int = 0
    danmaku_count: int = 0
    duration: str = ""
    pubdate: int = 0
    keyword: str = ""
    rank: int = 0                       # 在最终列表中的名次（1 起）
    tags: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        """转为可 JSON 序列化的字典。"""
        return asdict(self)


def _clean_title(raw: str) -> str:
    """去掉搜索接口返回的 <em class="keyword"> 高亮标签。"""
    return _TAG.sub("", raw or "").replace("&amp;", "&").strip()


def parse_search_item(item: Dict[str, object], keyword: str = "") -> Optional[VideoInfo]:
    """把搜索接口的一条结果解析成 VideoInfo。

    Args:
        item: 搜索接口 result 数组中的一项。
        keyword: 命中该结果的关键词。

    Returns:
        VideoInfo；缺少 bvid 时返回 None。
    """
    bvid = str(item.get("bvid") or "").strip()
    if not bvid:
        return None
    return VideoInfo(
        bvid=bvid,
        title=_clean_title(str(item.get("title") or "")),
        author=str(item.get("author") or ""),
        mid=int(item.get("mid") or 0),
        play=int(item.get("play") or 0),
        danmaku_count=int(item.get("video_review") or item.get("danmaku") or 0),
        duration=str(item.get("duration") or ""),
        pubdate=int(item.get("pubdate") or 0),
        keyword=keyword,
    )


def search_page(
    client: BiliClient,
    keyword: str,
    page: int,
    page_size: int = config.PAGE_SIZE,
    order: str = config.SEARCH_ORDER,
) -> List[VideoInfo]:
    """抓取搜索结果的一页。"""
    params = client.sign_params(
        {
            "search_type": "video",
            "keyword": keyword,
            "order": order,
            "page": page,
            "page_size": page_size,
        }
    )
    data = client.get_json(API_SEARCH, params)
    results = data.get("result") or []
    videos = [v for v in (parse_search_item(it, keyword) for it in results) if v]
    logger.debug("关键词[%s] 第 %d 页：%d 条", keyword, page, len(videos))
    return videos


def search_videos(
    client: BiliClient,
    keywords: Optional[Iterable[str]] = None,
    target: int = config.TARGET_VIDEO_COUNT,
) -> List[VideoInfo]:
    """多关键词分页抓取并按 bvid 去重，返回前 target 个视频。

    按关键词顺序轮转取页，保证每个关键词都有代表视频进入榜单，
    而不是被第一个关键词占满。

    Args:
        client: BiliClient 实例。
        keywords: 关键词列表，默认取 config.KEYWORDS。
        target: 目标视频数量。

    Returns:
        去重后的 VideoInfo 列表，rank 字段已填好。
    """
    kws = list(keywords or config.KEYWORDS)
    pages_needed = (target // config.PAGE_SIZE) + 2
    seen: Dict[str, VideoInfo] = {}
    per_keyword: Dict[str, int] = {k: 0 for k in kws}

    # 轮转：第 1 轮取每个关键词的第 1 页，第 2 轮取第 2 页，以此类推
    for page in range(1, pages_needed + 1):
        for kw in kws:
            if len(seen) >= target:
                break
            try:
                videos = search_page(client, kw, page)
            except Exception as exc:  # noqa: BLE001 - 单页失败不应中断整体
                logger.warning("关键词[%s] 第 %d 页抓取失败：%s", kw, page, exc)
                continue
            for v in videos:
                if v.bvid not in seen and len(seen) < target:
                    v.rank = len(seen) + 1
                    seen[v.bvid] = v
                    per_keyword[kw] += 1
            time.sleep(0)  # 让出 GIL，保持与其他阶段一致的节流节奏
        logger.info("已完成 %d 个关键词第 %d 页，累计去重视频 %d 个", len(kws), page, len(seen))
        if len(seen) >= target:
            break

    videos = list(seen.values())
    for idx, v in enumerate(videos, start=1):
        v.rank = idx
    logger.info("视频检索完成，共 %d 个（各关键词贡献：%s）", len(videos), per_keyword)
    return videos
