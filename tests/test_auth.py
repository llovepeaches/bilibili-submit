"""登录 / 会话层测试。

最重要的一组是 :func:`test_new_session_sets_browser_headers` 与
:func:`test_bare_session_identifies_as_python_requests`：
B 站会**识别脚本客户端的 UA**（``python-requests/2.x``），
对这类请求返回 **HTTP 412 的 HTML 风控页**而不是 JSON。
调用方的 ``.json()`` 于是炸成 ``JSONDecodeError``，
用户只看到「Expecting value: line 1 column 1」，完全无从排查。

这两条测试就是把这个坑钉死：一旦有人再写回 ``requests.Session()``，
登录流程就会重新暴露脚本身份而拿不到二维码。

全部用例都用假响应，不打真实网络——测试不该依赖 B 站在线。
"""

import json
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit import auth  # noqa: E402
from bilibili_submit.auth import new_session, poll_qrcode, request_qrcode  # noqa: E402
from bilibili_submit.client import USER_AGENT  # noqa: E402
from bilibili_submit.exceptions import ApiChangedError, NetworkError  # noqa: E402


# ---------- 请求头 ----------


def test_new_session_sets_browser_headers():
    """会话必须带 UA + Referer，否则 B 站直接 412。"""
    sess = new_session()
    assert sess.headers.get("User-Agent") == USER_AGENT
    assert sess.headers.get("Referer") == "https://passport.bilibili.com/login"


def test_new_session_applies_proxy_to_both_schemes():
    """代理要同时覆盖 http/https，只设一个等于没设。"""
    sess = new_session("http://127.0.0.1:7890")
    assert sess.proxies["http"] == "http://127.0.0.1:7890"
    assert sess.proxies["https"] == "http://127.0.0.1:7890"


def test_new_session_without_proxy_leaves_proxies_empty():
    sess = new_session()
    assert not sess.proxies


def test_new_session_is_exported_from_package():
    """``new_session`` 是给 UI/CLI 共用的公开 API，必须在 __all__ 里。"""
    assert "new_session" in auth.__all__


def test_no_bare_session_left_in_login_paths():
    """UI 与 CLI 都必须走 new_session，否则登录页会拿不到二维码。

    这条是本组测试存在的原因：登录流程从auth 模块下沉到界面层时，
    一旦有人图省事直接 ``requests.Session()``，风控问题会立刻复发。
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = [
        os.path.join(root, "bilibili_submit", "ui", "views", "login.py"),
        os.path.join(root, "bilibili_submit", "cli.py"),
    ]
    for path in targets:
        with open(path, encoding="utf-8") as fh:
            code = fh.read()
        # 去掉注释和docstring 再找，避免文档里举例也被算进去
        stripped = "\n".join(
            line for line in code.splitlines() if not line.lstrip().startswith("#")
        )
        assert "requests.Session()" not in stripped, (
            f"{os.path.basename(path)} 里有裸 requests.Session()，"
            "必须改用 auth.new_session()（缺 UA 会被风控 412）"
        )


def test_bare_session_identifies_as_python_requests():
    """反证：裸 Session 的 UA 暴露脚本身份，这正是 412 的成因。

    不是真的打 B 站（会依赖网络），而是比对 UA：
    裸 Session 带的是 ``python-requests/x.y.z``，会被 B 站识别为脚本客户端；
    ``new_session`` 才伪装成桌面 Chrome。
    """
    bare = requests.Session()
    with_ua = new_session()

    bare_ua = bare.headers.get("User-Agent", "")
    assert bare_ua.startswith("python-requests/"), (
        f"裸 Session 的 UA 变成了 {bare_ua!r}——requests 可能改了默认值，"
        "这条测试需要跟着更新"
    )
    assert bare_ua != with_ua.headers.get("User-Agent")
    assert "Chrome" in with_ua.headers["User-Agent"]


# ---------- 响应解析 ----------


class _FakeResponse:
    """够用的假响应，只实现测试碰到的部分。"""

    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


def test_json_or_raise_raises_network_error_on_412():
    """412 应转成带排查建议的 NetworkError，而不是 JSONDecodeError。"""
    resp = _FakeResponse(status_code=412, payload=None, text="<!DOCTYPE html><html>")
    with pytest.raises(NetworkError) as exc:
        auth._json_or_raise(resp, "获取二维码")
    assert "412" in str(exc.value)
    assert "风控" in str(exc.value.hint)


def test_json_or_raise_raises_api_changed_error_on_html_200():
    """B 站偶尔用 200 返HTML 风控页，同样要给出可读原因。"""
    resp = _FakeResponse(status_code=200, payload=None, text="<html>nope</html>")
    with pytest.raises(ApiChangedError):
        auth._json_or_raise(resp, "获取二维码")


def test_json_or_raise_passes_through_normal_payload():
    resp = _FakeResponse(payload={"code": 0, "data": {"url": "https://x"}})
    assert auth._json_or_raise(resp, "获取二维码")["code"] == 0


def test_json_or_raise_rejects_non_object_json():
    """顶层是 list 时 ``payload.get`` 会炸，得提前拦下。"""
    resp = _FakeResponse(payload=[1, 2, 3])
    with pytest.raises(ApiChangedError) as exc:
        auth._json_or_raise(resp, "获取二维码")
    assert "list" in str(exc.value)


# ---------- 二维码申请 ----------


class _FakeSession:
    def __init__(self, response, headers=None):
        self.headers = headers or {}
        self.proxies = {}
        self._response = response

    def get(self, url, params=None, timeout=None):
        self.last_url = url
        self.last_params = params
        return self._response

    @property
    def cookies(self):
        return []


def test_request_qrcode_returns_key_and_url():
    payload = {
        "code": 0,
        "message": "OK",
        "data": {"qrcode_key": "KEY123", "url": "https://passport/qr?k=KEY123"},
    }
    qr = request_qrcode(_FakeSession(_FakeResponse(payload=payload)))
    assert qr.key == "KEY123"
    assert qr.url.endswith("KEY123")


def test_request_qrcode_surfaces_business_code():
    payload = {"code": -3, "message": "参数错误"}
    with pytest.raises(auth.BiliError) as exc:
        request_qrcode(_FakeSession(_FakeResponse(payload=payload)))
    assert "参数错误" in str(exc.value)


def test_request_qrcode_uses_browser_headers_by_default(monkeypatch):
    """不传 session 时也要用带 UA 的会话，否则必被风控。"""
    seen = {}

    def fake_new_session(proxy=None):
        seen["proxy"] = proxy
        return _FakeSession(
            _FakeResponse(payload={"code": 0, "data": {"qrcode_key": "k", "url": "u"}}),
            headers={"User-Agent": USER_AGENT},
        )

    monkeypatch.setattr(auth, "new_session", fake_new_session)
    request_qrcode()
    assert "proxy" in seen


# ---------- 轮询 ----------


def test_poll_qrcode_reads_inner_code():
    """poll 是双层错误码：外层 0，真实状态在 data.code。"""
    payload = {"code": 0, "data": {"code": 86090, "message": "已扫码未确认"}}
    status = poll_qrcode("KEY", _FakeSession(_FakeResponse(payload=payload)))
    assert status.code == 86090
    assert status.scanned and not status.done


def test_poll_qrcode_extracts_cookies_from_redirect_url():
    """跨域时 cookie 可能没进 session，要能从返回 url兜底解析。"""
    payload = {
        "code": 0,
        "data": {
            "code": 0,
            "message": "确认登录",
            "url": (
                "https://www.bilibili.com/correspond?SESSDATA=sess_value"
                "&bili_jct=jct_value&DedeUserID=123"
            ),
        },
    }
    status = poll_qrcode("KEY", _FakeSession(_FakeResponse(payload=payload)))
    assert status.done
    assert status.cookies["SESSDATA"] == "sess_value"
    assert status.cookies["bili_jct"] == "jct_value"
    assert status.cookies["DedeUserID"] == "123"


def test_poll_qrcode_missing_inner_code_is_error():
    payload = {"code": 0, "data": {"message": "没有 code 字段"}}
    with pytest.raises(auth.BiliError) as exc:
        poll_qrcode("KEY", _FakeSession(_FakeResponse(payload=payload)))
    assert "data.code" in str(exc.value)


@pytest.mark.parametrize(
    "code, expect_done, expect_expired",
    [
        (0, True, False),
        (86038, False, True),
        (86090, False, False),
        (86101, False, False),
    ],
)
def test_poll_status_properties(code, expect_done, expect_expired):
    status = auth.LoginStatus(code=code, message="", cookies={})
    assert status.done is expect_done
    assert status.expired is expect_expired
