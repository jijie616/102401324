# -*- coding: utf-8 -*-
"""B 站客户端关键算法（wbi 签名）与容错行为的单元测试。

这些用例不发真实网络请求：通过传入预先构造的 wbi key 直接验证签名算法，
用替身 Session 验证重试与错误码处理。
"""
from __future__ import annotations

import hashlib
import urllib.parse

import pytest
import requests

from llm_danmaku.bili_client import BiliApiError, BiliClient


class DummyResponse:
    """requests.Response 的最小替身。"""

    def __init__(self, payload=None, status_code: int = 200, content: bytes = b"") -> None:
        self._payload = payload or {}
        self.status_code = status_code
        self.content = content

    def json(self):
        """返回预设 JSON。"""
        return self._payload

    def raise_for_status(self) -> None:
        """非 200 时抛异常，模拟真实行为。"""
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class DummySession:
    """按顺序返回预设响应的 Session 替身，并记录调用次数。

    需要同时支持 prepare_request + send 两个方法，因为客户端为避免
    并发持锁而改用「先准备请求、再在锁外发送」的方式。
    """

    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls = 0
        self.headers = {}
        self.cookies = _DummyCookies()

    def prepare_request(self, request):  # noqa: D401
        """返回请求本身（替身无需真正准备）。"""
        return request

    def send(self, request, timeout=None, **kwargs):  # noqa: D401
        """返回下一个预设响应。"""
        return self._next()

    def get(self, url, params=None, timeout=None):  # noqa: D401
        """兼容直接调用 get 的场景。"""
        return self._next()

    def _next(self):
        """取下一个预设响应，耗尽则抛连接异常。"""
        self.calls += 1
        if not self._responses:
            raise requests.ConnectionError("没有更多预设响应")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        """关闭替身（无操作）。"""


class _DummyCookies:
    """Cookie 罐替身（只需支持 set 即可验证注入行为）。"""

    def __init__(self) -> None:
        self.store = {}

    def set(self, name, value, domain=None) -> None:
        """记录写入的 cookie。"""
        self.store[name] = value


def make_client(**kwargs) -> BiliClient:
    """构造一个不发请求的客户端（delay 与全局节流都置 0 以加速测试）。"""
    kwargs.setdefault("delay_range", (0, 0))
    kwargs.setdefault("max_retries", 2)
    kwargs.setdefault("min_interval", 0.0)
    return BiliClient(**kwargs)


def make_signed_client(key: str) -> BiliClient:
    """构造一个已注入 wbi key 的客户端，避免测试触发真实网络请求。

    `_wbi_key_ts` 必须同步刷新，否则会被判定为过期而重新请求 nav 接口。
    """
    import time as _time

    client = make_client()
    client._wbi_key = key          # noqa: SLF001 - 测试注入
    client._wbi_key_ts = _time.time()   # noqa: SLF001
    return client


# ---------------------------------------------------------------- TC27
def test_wbi_signature_确定性():
    """TC27：相同参数与 wts 必须得到相同签名，且 w_rid 为 32 位小写 MD5。"""
    client = make_signed_client("0123456789abcdef0123456789abcdef")

    params = {"search_type": "video", "keyword": "大模型", "page": 1}
    first = client.sign_params(params, wts=1700000000)
    second = client.sign_params(params, wts=1700000000)

    assert first["w_rid"] == second["w_rid"]
    assert len(first["w_rid"]) == 32
    assert first["w_rid"] == first["w_rid"].lower()
    assert first["wts"] == 1700000000
    # 不修改入参
    assert "w_rid" not in params


# ---------------------------------------------------------------- TC28
def test_wbi_signature_算法可复算():
    """TC28：按文档规则手工复算的 MD5 应与实现一致（算法正确性的强断言）。"""
    key = "fedcba9876543210fedcba9876543210"
    client = make_signed_client(key)

    params = {"keyword": "大语言模型", "page": 2, "search_type": "video"}
    wts = 1712345678
    signed = client.sign_params(params, wts=wts)

    # 手工复算：参数按 key 升序、剔除 !'()*、urlencode 后拼接 key 做 MD5
    items = sorted({**params, "wts": wts}.items(), key=lambda kv: kv[0])
    query = urllib.parse.urlencode(
        [(k, str(v).translate(str.maketrans("", "", "!'()*"))) for k, v in items]
    )
    expected = hashlib.md5((query + key).encode()).hexdigest()
    assert signed["w_rid"] == expected


# ---------------------------------------------------------------- TC29
def test_signature_改变参数会改变签名():
    """TC29：任一参数变化都必须导致签名变化（防篡改有效性）。"""
    client = make_signed_client("abcdef0123456789abcdef0123456789")

    a = client.sign_params({"keyword": "大模型", "page": 1}, wts=1700000000)
    b = client.sign_params({"keyword": "大模型", "page": 2}, wts=1700000000)
    c = client.sign_params({"keyword": "大模型2", "page": 1}, wts=1700000000)
    assert len({a["w_rid"], b["w_rid"], c["w_rid"]}) == 3


# ---------------------------------------------------------------- TC30
def test_get_json_业务错误码抛异常():
    """TC30：code != 0 时应抛 BiliApiError 并带上错误码。"""
    session = DummySession([DummyResponse({"code": -412, "message": "请求被拦截"})])
    client = make_client(session=session)
    with pytest.raises(BiliApiError) as exc:
        client.get_json("https://api.bilibili.com/x/test")
    assert exc.value.code == -412
    assert "拦截" in exc.value.message


# ---------------------------------------------------------------- TC31
def test_get_网络异常会重试():
    """TC31：首次连接异常后应重试并最终成功（健壮性要求）。"""
    session = DummySession([
        requests.ConnectionError("网络抖动"),
        DummyResponse({"code": 0, "data": {"ok": True}}),
    ])
    client = make_client(session=session)
    data = client.get_json("https://api.bilibili.com/x/test")
    assert data == {"ok": True}
    assert session.calls == 2


# ---------------------------------------------------------------- TC32
def test_get_重试耗尽抛出():
    """TC32：重试耗尽后应抛出 RequestException，而不是静默返回空数据。"""
    session = DummySession([
        requests.ConnectionError("坏"),
        requests.ConnectionError("还是坏"),
    ])
    client = make_client(session=session)
    with pytest.raises(requests.RequestException):
        client.get("https://api.bilibili.com/x/test")


# ---------------------------------------------------------------- TC33
def test_binary_请求返回字节():
    """TC33：binary=True 时返回原始字节，供 XML/Protobuf 解析使用。"""
    session = DummySession([DummyResponse(content=b"<i><d>hi</d></i>")])
    client = make_client(session=session)
    payload = client.get("https://api.bilibili.com/x/v1/dm/list.so", binary=True)
    assert isinstance(payload, bytes)
    assert b"<d>" in payload


# ---------------------------------------------------------------- TC34
def test_sessdata_注入_cookie():
    """TC34：传入 SESSDATA 时应写入 Cookie（用于获取完整弹幕）。"""
    client = make_client(sessdata="test-token-123")
    jar = client.session.cookies
    value = jar.get("SESSDATA") if hasattr(jar, "get") else jar.store.get("SESSDATA")
    assert value == "test-token-123"
