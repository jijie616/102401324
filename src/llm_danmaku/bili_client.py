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
        min_interval: float = config.MIN_REQUEST_INTERVAL,
    ) -> None:
        self.delay_range = delay_range
        self.max_retries = max_retries
        self.timeout = timeout
        self._wbi_key: Optional[str] = None
        self._wbi_key_ts = 0.0
        self._lock = threading.Lock()          # 保护 wbi key / Session 字典 / 令牌桶
        self._sessdata = (sessdata if sessdata is not None else config.SESSDATA).strip()

        # 全局令牌桶与熔断状态
        self._min_interval = max(0.0, float(min_interval))
        self._last_request_ts = 0.0
        self._consecutive_failures = 0
        self._cooldown_until = 0.0
        self._cooldown_seconds = 0.0      # 当前冷却时长，触发熔断时按倍数增长
        self._last_cooldown_log = 0.0     # 冷却日志节流用

        # 线程专属 Session 字典：requests.Session 的 CookieJar 在并发写时不是线程安全的，
        # 每个抓取线程各持一个 Session 才能真正并发。
        # 若外部显式传入 session（单元测试注入替身），则所有线程共用它。
        self._external_session = session
        self._sessions: Dict[int, requests.Session] = {}
        if session is not None:
            self._configure(session)

    # ---------------------------------------------------------------- 会话管理
    @staticmethod
    def _configure(session: requests.Session) -> None:
        """给 Session 套用统一请求头。"""
        session.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Referer": "https://www.bilibili.com/",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )

    def _build_session(self) -> requests.Session:
        """新建一个已配置好的 Session（含 SESSDATA 注入）。"""
        session = requests.Session()
        self._configure(session)
        if self._sessdata:
            session.cookies.set("SESSDATA", self._sessdata, domain=".bilibili.com")
        return session

    def _thread_session(self) -> requests.Session:
        """取当前线程专属的 Session。

        实现要点：**Session 的创建绝不能放在锁内**。
        ``requests.Session()`` 首次构造会初始化 SSL 上下文与证书校验，
        耗时可达数百毫秒；若在持锁期间构造，6 个抓取线程会串行排队等待，
        表现就是"所有线程都卡在取 Session 上、几分钟没有任何产出"。

        因此采用双重检查：锁内只做字典查找，未命中则**出锁构造**，再入锁登记。
        requests.Session 的 CookieJar 不是并发安全的，所以每个线程必须独立持有。
        """
        if self._external_session is not None:
            return self._external_session

        ident = threading.get_ident()
        with self._lock:                       # 临界区仅一次字典查找
            session = self._sessions.get(ident)
        if session is not None:
            return session

        session = self._build_session()        # 出锁构造（SSL 初始化在这里完成）

        with self._lock:                       # 二次检查 + 登记
            existing = self._sessions.get(ident)
            if existing is not None:
                session.close()
                return existing
            self._sessions[ident] = session
        return session

    @property
    def session(self) -> requests.Session:
        """当前线程的 Session（对外保留该属性名以兼容调用方与测试）。"""
        return self._thread_session()

    def close(self) -> None:
        """释放本客户端创建的所有连接池。"""
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            try:
                session.close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不应影响退出
                logger.debug("Session 关闭异常：%s", exc)

    # ---------------------------------------------------------------- 基础请求
    def _sleep(self) -> None:
        """随机休眠，降低被风控的概率。"""
        low, high = self.delay_range
        if high > 0:
            time.sleep(random.uniform(low, high))

    def _throttle(self) -> None:
        """进程级令牌桶：保证任意两次请求之间至少间隔 MIN_REQUEST_INTERVAL。

        这是**全局**节流（跨线程共享），因此把并发调高也不会突破总速率上限。
        实测：无节流时 6 线程短时间打出数千请求会触发 B 站突发限流，
        接口连续失败直至冷却；加入全局节流后可持续稳定抓取。
        """
        with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last_request_ts)
            if wait > 0:
                time.sleep(wait)
            self._last_request_ts = time.monotonic()
        # 熔断冷却：连续失败过多时全局暂停，等待风控解除
        while True:
            with self._lock:
                remaining = self._cooldown_until - time.monotonic()
            if remaining <= 0:
                return
            # 多个工作线程会同时等待冷却，这里做日志节流避免刷屏
            now = time.monotonic()
            with self._lock:
                should_log = now - self._last_cooldown_log >= 15.0
                if should_log:
                    self._last_cooldown_log = now
            if should_log:
                logger.warning("熔断冷却中，剩余 %.1fs（等待风控解除）", remaining)
            time.sleep(min(remaining, 5.0))

    def _record_success(self) -> None:
        """记录一次成功请求，清零连续失败计数。"""
        with self._lock:
            self._consecutive_failures = 0

    def _record_failure(self, reason: str) -> None:
        """记录一次失败；连续失败超阈值则进入冷却（熔断，冷却时长指数增长）。"""
        with self._lock:
            self._consecutive_failures += 1
            if (self._consecutive_failures >= config.CIRCUIT_FAIL_THRESHOLD
                    and time.monotonic() >= self._cooldown_until):
                self._cooldown_seconds = min(
                    config.CIRCUIT_COOLDOWN_MAX,
                    max(config.CIRCUIT_COOLDOWN,
                        self._cooldown_seconds * config.CIRCUIT_COOLDOWN_FACTOR),
                )
                self._cooldown_until = time.monotonic() + self._cooldown_seconds
                logger.warning("连续失败 %d 次（%s），触发熔断，冷却 %.0fs",
                               self._consecutive_failures, reason,
                               self._cooldown_seconds)
                self._consecutive_failures = 0

    def get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        binary: bool = False,
        retries: Optional[int] = None,
    ) -> Any:
        """GET 请求，自动重试 + 全局节流 + 熔断保护。

        **线程安全说明**：锁只用于令牌桶计数与字典查找（微秒级），
        绝不能在持锁期间做网络等待，也不能在锁内再调用会取同一把锁的方法
        （``threading.Lock`` 不可重入，会直接自死锁）。

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
        # 只取一次 Session（_thread_session 内部自带锁，这里绝不能再套一层）
        session = self._thread_session()

        for attempt in range(1, attempts + 1):
            self._sleep()
            self._throttle()
            try:
                resp = session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 412:
                    # 412 是 B 站的风控信号，必须显著退避
                    wait = config.BACKOFF_BASE ** (attempt + 2)
                    logger.warning("触发风控 412，退避 %.1fs 后重试（第 %d 次）", wait, attempt)
                    self._record_failure("HTTP 412")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                self._record_success()
                if binary:
                    return resp.content
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - 需覆盖网络/解析各类异常
                last_error = exc
                wait = config.BACKOFF_BASE ** attempt
                logger.debug("请求失败(%s)，%.1fs 后重试：%s", type(exc).__name__, wait, exc)
                self._record_failure(type(exc).__name__)
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

    def __enter__(self) -> "BiliClient":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()
