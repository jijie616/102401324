# -*- coding: utf-8 -*-
"""B 站 HTTP 客户端：统一处理请求头、限速、重试、wbi 签名与登录态。

对外只暴露 BiliClient 一个类，爬虫各模块通过它访问网络，
便于单元测试时注入替身（依赖倒置，满足低耦合要求）。
"""
from __future__ import annotations

import hashlib
import logging
import random
import threading
import time
import urllib.parse
from typing import Any, Dict, Optional

import requests

from . import config

logger = logging.getLogger(__name__)

# wbi 签名用的固定重排表（B 站前端 JS 中的 mixinKeyEncTab）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]

API_SEARCH = "https://api.bilibili.com/x/web-interface/wbi/search/type"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_DANMAKU_XML = "https://api.bilibili.com/x/v1/dm/list.so"
API_DANMAKU_PROTO = "https://api.bilibili.com/x/v2/dm/web/seg.so"

# 取 cid 的三个候选接口（按优先级）。
# 注意：x/web-interface/view 自 2025 年起对匿名请求持续返回 412（请求被封），
# 实测可用的是 player/pagelist 与 player/v2，因此把它们作为首选。
API_PAGELIST = "https://api.bilibili.com/x/player/pagelist"
API_PLAYER_V2 = "https://api.bilibili.com/x/player/v2"
API_VIEW = "https://api.bilibili.com/x/web-interface/view"   # 仅作最后兜底


class BiliApiError(RuntimeError):
    """B 站接口返回了非 0 的业务错误码。"""

    def __init__(self, code: int, message: str, url: str = "") -> None:
        super().__init__(f"[{code}] {message} ({url})")
        self.code = code
        self.message = message
        self.url = url


class BiliClient:
    """带限速与重试的 B 站 API 客户端。

    线程安全：内部 Session 仅在加锁后使用，可被多线程共享。
    """

    def __init__(
        self,
        sessdata: Optional[str] = None,
        delay_range: tuple = config.REQUEST_DELAY,
        max_retries: int = config.MAX_RETRIES,
        timeout: int = config.REQUEST_TIMEOUT,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.delay_range = delay_range
        self.max_retries = max_retries
        self.timeout = timeout
        self._lock = threading.Lock()
        self._wbi_key: Optional[str] = None
        self._wbi_key_ts = 0.0

        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        token = (sessdata if sessdata is not None else config.SESSDATA)
        if token:
            self.session.cookies.set("SESSDATA", token, domain=".bilibili.com")
            logger.info("已注入 SESSDATA，可获取更完整的弹幕数据")

    # ---------------------------------------------------------------- 基础请求
    def _sleep(self) -> None:
        """随机休眠，降低被风控的概率。"""
        low, high = self.delay_range
        if high > 0:
            time.sleep(random.uniform(low, high))

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        binary: bool = False,
        retries: Optional[int] = None,
    ) -> Any:
        """GET 请求，自动重试。

        Args:
            url: 目标地址。
            params: 查询参数。
            binary: True 返回 bytes（XML/Protobuf 弹幕），False 返回 JSON dict。
            retries: 覆盖默认重试次数。

        Returns:
            JSON dict 或 bytes。

        Raises:
            requests.RequestException: 重试耗尽后仍失败。
        """
        attempts = self.max_retries if retries is None else retries
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            self._sleep()
            try:
                with self._lock:
                    resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 412:
                    # 412 是 B 站的风控信号，必须显著退避
                    wait = config.BACKOFF_BASE ** (attempt + 2)
                    logger.warning("触发风控 412，退避 %.1fs 后重试（第 %d 次）", wait, attempt)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                if binary:
                    return resp.content
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - 需覆盖网络/解析各类异常
                last_error = exc
                wait = config.BACKOFF_BASE ** attempt
                logger.debug("请求失败(%s)，%.1fs 后重试：%s", type(exc).__name__, wait, exc)
                time.sleep(wait)

        raise requests.RequestException(
            f"请求重试 {attempts} 次仍失败: {url} params={params}"
        ) from last_error

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """GET 并在 code != 0 时抛 BiliApiError。"""
        data = self.get(url, params)
        if not isinstance(data, dict):
            raise BiliApiError(-1, f"响应不是 JSON 对象: {type(data)}", url)
        if data.get("code") != 0:
            raise BiliApiError(int(data.get("code", -1)), str(data.get("message", "")), url)
        return data.get("data") or {}

    # ------------------------------------------------------------------ wbi
    def _fetch_wbi_key(self) -> str:
        """从 nav 接口取 img_key/sub_url 并重排得到 mixinKey。

        注意：B 站的 nav 接口对**未登录**请求会返回 code=-101（账号未登录），
        但响应体中**依然带有 wbi_img**。因此这里不能因为 code!=0 就放弃，
        必须直接从原始响应里取 wbi_img，否则匿名爬取会直接失败。
        """
        resp = self.get(API_NAV)
        if not isinstance(resp, dict):
            raise BiliApiError(-1, "nav 接口响应异常", API_NAV)

        data = resp.get("data") or {}
        wbi = data.get("wbi_img") or {}
        img_key = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
        sub_key = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]

        if not img_key or not sub_key:
            code = resp.get("code")
            raise BiliApiError(
                int(code if code is not None else -1),
                f"nav 接口未返回 wbi_img，无法签名：{resp.get('message', '')}",
                API_NAV,
            )

        if resp.get("code") != 0:
            logger.debug("nav 接口 code=%s（%s），但已成功取得 wbi_img，继续匿名访问",
                         resp.get("code"), resp.get("message"))

        raw = img_key + sub_key
        return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]

    def wbi_key(self, ttl: float = 3600.0) -> str:
        """获取（带缓存的）wbi mixinKey。"""
        now = time.time()
        if self._wbi_key is None or now - self._wbi_key_ts > ttl:
            self._wbi_key = self._fetch_wbi_key()
            self._wbi_key_ts = now
            logger.debug("刷新 wbi key: %s", self._wbi_key)
        return self._wbi_key

    @staticmethod
    def _strip_forbidden(value: str) -> str:
        """按 B 站规则剔除 !'()* 五个字符。"""
        return value.translate(str.maketrans("", "", "!'()*"))

    def sign_params(self, params: Dict[str, Any], wts: Optional[int] = None) -> Dict[str, Any]:
        """为参数追加 wts 与 w_rid，返回新字典（不修改入参）。"""
        signed = {k: v for k, v in params.items() if v is not None}
        signed["wts"] = int(time.time()) if wts is None else wts
        items = sorted(signed.items(), key=lambda kv: kv[0])
        query = urllib.parse.urlencode(
            [(k, self._strip_forbidden(str(v))) for k, v in items]
        )
        signed["w_rid"] = hashlib.md5((query + self.wbi_key()).encode()).hexdigest()
        return signed

    def close(self) -> None:
        """释放连接池。"""
        self.session.close()

    def __enter__(self) -> "BiliClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
