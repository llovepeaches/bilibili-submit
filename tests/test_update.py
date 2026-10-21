"""检查更新的测试。

**全程离线**：一次真的 HTTP 请求都不发。GitHub 那点限额（未认证
60 次/小时/IP）经不起测试反复打，而且联网测试会变成随机失败。

重点盯三类东西：

1. 版本比较的**数值**语义——按字符串比会得出 ``0.2.10 < 0.2.6``，
   第十个修订版反而提示不出来；
2. 各种失败都要**静默**——403 限流、404 没发过版、断网、响应不是
   JSON，都得返回 ``None`` 而不是抛异常；
3. 限频真的挡住了请求——不是「看起来查了」；
4. **更新在客户端内完成**——查得到资产、下得动、下得完、按形态
   落地（安装器 / 自替换），全程不开浏览器。
"""

import json
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit import __version__  # noqa: E402
from bilibili_submit import update as upd  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------- 替身 ----------


class _FakeResponse:
    """够用的假响应。"""

    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("Expecting value: line 1 column 1 (char 0)")
        return self._payload


class _FakeStreamResponse:
    """下载用的假响应：支持 ``iter_content`` 流式吐字节。"""

    def __init__(self, content=b"", status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._content = content
        self.closed = False

    def iter_content(self, chunk_size=64 * 1024):
        for start in range(0, len(self._content), chunk_size):
            yield self._content[start:start + chunk_size]

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, response=None, exc=None):
        self.headers = {}
        self.proxies = {}
        self.closed = False
        self.last_url = None
        self.last_timeout = None
        self.last_stream = None
        self._response = response
        self._exc = exc

    def get(self, url, timeout=None, stream=False):
        self.last_url = url
        self.last_timeout = timeout
        self.last_stream = stream
        if self._exc is not None:
            raise self._exc
        return self._response

    def close(self):
        self.closed = True


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(upd, "_new_session", lambda proxy=None: session)


def _release(tag="v9.9.9", url="https://example.test/release", prerelease=False, assets=None):
    payload = {"tag_name": tag, "html_url": url, "prerelease": prerelease}
    if assets is not None:
        payload["assets"] = assets
    return _FakeResponse(payload=payload)


#: release 里常见的六个资产（名字与 release.yml 上传的一致）
FAKE_ASSETS = [
    {
        "name": "bilibili-submit-setup.exe",
        "browser_download_url": "https://d/setup.exe",
        "size": 33_000_000,
    },
    {
        "name": "bilibili-submit-gui-portable.exe",
        "browser_download_url": "https://d/gui-portable.exe",
        "size": 72_000_000,
    },
    {
        "name": "bilibili-submit-cli-portable.exe",
        "browser_download_url": "https://d/cli-portable.exe",
        "size": 70_000_000,
    },
    {
        "name": "bilibili-submit-cli.exe",
        "browser_download_url": "https://d/cli.exe",
        "size": 11_000_000,
    },
    {
        "name": "bilibili-submit-cli-full.zip",
        "browser_download_url": "https://d/full.zip",
        "size": 41_000_000,
    },
    {
        "name": "bilibili-submit-cli-mini.zip",
        "browser_download_url": "https://d/mini.zip",
        "size": 10_000_000,
    },
]


# ---------- 版本比较 ----------


def test_parse_strips_the_v_prefix():
    assert upd.parse_version("v0.2.7") == ((0, 2, 7), "")


def test_parse_keeps_the_prerelease_suffix():
    assert upd.parse_version("v0.3.0-rc.1") == ((0, 3, 0), "rc.1")


@pytest.mark.parametrize("tag", ["latest", "nightly", "v1.x", "", "v", "0.2.7.x"])
def test_parse_rejects_non_numeric_core(tag):
    """认不出的 tag 一律返回 None，而不是硬比出一个结论。"""
    assert upd.parse_version(tag) is None


def test_compare_is_numeric_not_lexicographic():
    """0.2.10 比 0.2.6 新。按字符串比会得出相反的结论。"""
    assert upd.is_newer("v0.2.10", "0.2.6") is True
    assert upd.is_newer("v0.2.6", "0.2.10") is False


def test_equal_and_older_are_not_newer():
    assert upd.is_newer("v0.2.6", "0.2.6") is False
    assert upd.is_newer("v0.2.5", "0.2.6") is False


def test_current_version_is_not_newer_than_itself():
    """当前版本不该提示自己——发版时最容易漏的就是这条。"""
    assert upd.is_newer(f"v{__version__}", __version__) is False


def test_prerelease_is_hidden_by_default():
    assert upd.is_newer("v0.3.0-rc.1", "0.2.6") is False


def test_prerelease_counts_when_enabled():
    assert upd.is_newer("v0.3.0-rc.1", "0.2.6", allow_prerelease=True) is True


def test_prerelease_ordering_when_enabled():
    """beta 排在 alpha 后面，rc 排在 beta 后面，正式版最后。"""
    assert upd.is_newer("v0.3.0-beta.2", "v0.3.0-beta.1", True) is True
    assert upd.is_newer("v0.3.0-rc.1", "v0.3.0-beta.9", True) is True
    assert upd.is_newer("v0.3.0", "v0.3.0-rc.1", True) is True


def test_build_metadata_is_ignored():
    assert upd.parse_version("v0.2.7+build.5") == ((0, 2, 7), "")


# ---------- 拉取 ----------


def test_fetch_returns_the_release(monkeypatch):
    session = _FakeSession(_release("v0.2.7"))
    _patch_session(monkeypatch, session)

    info = upd.fetch_latest_release()

    assert info is not None
    assert info.version == "0.2.7"
    assert info.tag == "v0.2.7"
    assert info.url == "https://example.test/release"
    assert session.closed is True, "会话用完要关，否则连接一直挂着"


def test_fetch_hits_the_latest_release_api(monkeypatch):
    session = _FakeSession(_release())
    _patch_session(monkeypatch, session)
    upd.fetch_latest_release()

    assert session.last_url == upd.API_URL
    assert session.last_url.endswith("/releases/latest")


def test_user_agent_is_always_set():
    """GitHub API 对没有 UA 的请求直接 403。

    这个坑不会在别处报错——响应体照样是 JSON，只是状态 403，看起来和
    撞了限频一模一样。所以这里查真实的会话构造，不打桩。
    """
    session = upd._new_session()

    assert "bilibili-submit/" in session.headers.get("User-Agent", "")


def test_timeout_is_short(monkeypatch):
    """检查更新不该拖慢启动：连不上最多等几秒，不是默认的 30 秒。"""
    session = _FakeSession(_release())
    _patch_session(monkeypatch, session)
    upd.fetch_latest_release()

    connect, read = session.last_timeout
    assert connect <= 5 and read <= 10


@pytest.mark.parametrize("status", [403, 404, 500, 301])
def test_non_200_returns_none(monkeypatch, status):
    """403 是撞了限频，404 是还没发过版。都不是错误，静默跳过即可。"""
    _patch_session(monkeypatch, _FakeSession(_FakeResponse(status_code=status, text="")))
    assert upd.fetch_latest_release() is None


def test_bad_json_returns_none(monkeypatch):
    _patch_session(monkeypatch, _FakeSession(_FakeResponse(text="<html>nope")))
    assert upd.fetch_latest_release() is None


def test_network_error_returns_none(monkeypatch):
    import requests

    _patch_session(monkeypatch, _FakeSession(exc=requests.Timeout("timed out")))
    assert upd.fetch_latest_release() is None


def test_missing_tag_name_returns_none(monkeypatch):
    _patch_session(monkeypatch, _FakeSession(_FakeResponse(payload={"html_url": "u"})))
    assert upd.fetch_latest_release() is None


def test_url_falls_back_to_the_releases_page(monkeypatch):
    _patch_session(
        monkeypatch, _FakeSession(_FakeResponse(payload={"tag_name": "v0.2.7"}))
    )
    info = upd.fetch_latest_release()

    assert info.url == upd.RELEASES_URL


def test_proxy_is_applied(monkeypatch):
    seen = {}

    def fake(proxy=None):
        seen["proxy"] = proxy
        return _FakeSession(_release())

    monkeypatch.setattr(upd, "_new_session", fake)
    upd.fetch_latest_release(proxy="http://127.0.0.1:7890")

    assert seen["proxy"] == "http://127.0.0.1:7890"


# ---------- 限频与状态 ----------


def test_skips_when_checked_recently(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_check": time.time()}), encoding="utf-8")

    assert upd.should_check(upd._read_state(path)[0]) is False


def test_allows_after_a_day(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps({"last_check": time.time() - upd.CHECK_INTERVAL - 1}),
        encoding="utf-8",
    )

    assert upd.should_check(upd._read_state(path)[0]) is True


def test_check_interval_is_one_day():
    assert upd.CHECK_INTERVAL == 86400


def test_clock_rewind_does_not_look_like_a_recent_check():
    """时钟被回拨（或文件里存了未来时间）时按「没查过」处理。"""
    assert upd.should_check(time.time() + 3600) is True


def test_corrupt_state_counts_as_never_checked(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_check": "昨天"}), encoding="utf-8")

    assert upd._read_state(path)[0] == 0.0
    assert upd.should_check(upd._read_state(path)[0]) is True


def test_missing_file_counts_as_never_checked(tmp_path):
    assert upd._read_state(tmp_path / "nope.json") == (0.0, "")


def test_throttled_check_sends_no_request(tmp_path, monkeypatch):
    """被限频挡住时必须一次请求都不发，而不是「发了但没说」。"""
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_check": time.time()}), encoding="utf-8")
    calls = []
    monkeypatch.setattr(upd, "_new_session", lambda proxy=None: calls.append(1))

    assert upd.check_for_update(path=path) is None
    assert calls == []


def test_force_bypasses_the_throttle(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"last_check": time.time()}), encoding="utf-8")
    _patch_session(monkeypatch, _FakeSession(_release("v9.9.9")))

    assert upd.check_for_update(force=True, path=path) is not None


def test_successful_check_is_remembered(tmp_path, monkeypatch):
    _patch_session(monkeypatch, _FakeSession(_release("v0.2.6")))
    path = tmp_path / "state.json"

    upd.check_for_update(path=path)

    assert upd.should_check(upd._read_state(path)[0]) is False


def test_network_failure_is_not_remembered(tmp_path, monkeypatch):
    """断网那一次不算「查过了」，否则一整天都不会再试。"""
    import requests

    _patch_session(monkeypatch, _FakeSession(exc=requests.ConnectionError("down")))
    path = tmp_path / "state.json"

    upd.check_for_update(path=path)

    assert upd._read_state(path)[0] == 0.0


def test_rate_limited_check_is_remembered(tmp_path, monkeypatch):
    """403 是真收到了响应，记下来——否则限频期间每次启动都要撞一次。"""
    _patch_session(monkeypatch, _FakeSession(_FakeResponse(status_code=403, text="")))
    path = tmp_path / "state.json"

    upd.check_for_update(path=path)

    assert upd._read_state(path)[0] > 0


def test_disabled_by_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(upd.DISABLE_ENV, "0")
    calls = []
    monkeypatch.setattr(upd, "_new_session", lambda proxy=None: calls.append(1))

    assert upd.check_for_update(path=tmp_path / "s.json") is None
    assert calls == []


def test_state_roundtrip_keeps_both_fields(tmp_path):
    path = tmp_path / "state.json"
    upd._write_state(1700000000.0, "0.2.7", path)

    assert upd._read_state(path) == (1700000000.0, "0.2.7")


# ---------- 提示去重 ----------


def test_same_version_is_announced_once(tmp_path):
    path = tmp_path / "state.json"

    assert upd.should_notify("0.2.7", path) is True
    upd.mark_version_seen("0.2.7", path)
    assert upd.should_notify("0.2.7", path) is False
    assert upd.should_notify("0.2.8", path) is True


def test_mark_seen_keeps_the_timestamp(tmp_path):
    path = tmp_path / "state.json"
    upd._write_state(1700000000.0, "", path)
    upd.mark_version_seen("0.2.7", path)

    assert upd._read_state(path) == (1700000000.0, "0.2.7")


# ---------- 形态与文案 ----------


def test_source_form_is_detected(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert upd.release_form() == "source"


def test_installed_form_is_detected(tmp_path, monkeypatch):
    """onedir：``_internal`` 就在 exe 同目录。"""
    (tmp_path / "_internal").mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "bilibili-submit-gui.exe"))

    assert upd.release_form() == "installed"


def test_portable_form_is_detected(tmp_path, monkeypatch):
    """onefile：exe 同目录没有 ``_internal``（它在临时解压目录里）。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "gui-portable.exe"))

    assert upd.release_form() == "portable"


def test_hint_promises_in_app_update_when_installed(monkeypatch):
    monkeypatch.setattr(upd, "release_form", lambda: "installed")
    info = upd.ReleaseInfo("0.2.7", "v0.2.7", "https://example.test", False)

    title, body = upd.hint_text(info)

    assert title == "发现新版本 0.2.7"
    assert "下载新安装包" in body
    assert "退出一次" in body
    assert __version__ in body


def test_hint_promises_self_replacement_when_portable(monkeypatch):
    monkeypatch.setattr(upd, "release_form", lambda: "portable")
    info = upd.ReleaseInfo("0.2.7", "v0.2.7", "https://example.test", False)

    _title, body = upd.hint_text(info)

    assert "自动替换" in body
    assert "重启" in body
    assert "去下载页" not in body


def test_hint_points_source_users_at_the_code(monkeypatch):
    monkeypatch.setattr(upd, "release_form", lambda: "source")
    info = upd.ReleaseInfo("0.2.7", "v0.2.7", "https://example.test", False)

    _title, body = upd.hint_text(info)

    assert "重新拉取代码" in body


def test_open_release_page_uses_the_info_url(monkeypatch):
    opened = []
    monkeypatch.setattr(upd.webbrowser, "open", lambda url: opened.append(url) or True)

    upd.open_release_page("https://example.test/x")

    assert opened == ["https://example.test/x"]


def test_open_release_page_falls_back_to_releases(monkeypatch):
    opened = []
    monkeypatch.setattr(upd.webbrowser, "open", lambda url: opened.append(url) or True)

    upd.open_release_page(None)

    assert opened == [upd.RELEASES_URL]


# ---------- 资产选择 ----------


def test_release_info_carries_the_asset_by_default():
    """没带资产字段时构造不炸——CLI 与旧调用方都这么建。"""
    info = upd.ReleaseInfo("0.2.7", "v0.2.7", "https://example.test", False)

    assert info.asset_url is None
    assert info.asset_name is None
    assert info.asset_size == 0


def test_installed_form_downloads_the_installer(monkeypatch):
    """安装版没法自己替换 Program Files 下的文件，只能下载安装器。"""
    monkeypatch.setattr(upd, "release_form", lambda: "installed")
    _patch_session(monkeypatch, _FakeSession(_release("v0.2.7", assets=FAKE_ASSETS)))

    info = upd.fetch_latest_release()

    assert info.asset_name == "bilibili-submit-setup.exe"
    assert info.asset_url == "https://d/setup.exe"
    assert info.asset_size == 33_000_000


def test_portable_form_prefers_its_own_exe_name(tmp_path, monkeypatch):
    """便携版拿和自己同名的新 exe——GUI 便携版不该下到 CLI 便携版。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        sys, "executable", str(tmp_path / "bilibili-submit-gui-portable.exe")
    )
    monkeypatch.setattr(upd, "release_form", lambda: "portable")
    _patch_session(monkeypatch, _FakeSession(_release("v0.2.7", assets=FAKE_ASSETS)))

    info = upd.fetch_latest_release()

    assert info.asset_name == "bilibili-submit-gui-portable.exe"


def test_portable_form_falls_back_to_the_gui_asset(monkeypatch):
    """认不出自己是谁（没 frozen）时退回优先级列表的第一项。"""
    monkeypatch.setattr(upd, "release_form", lambda: "portable")
    _patch_session(monkeypatch, _FakeSession(_release("v0.2.7", assets=FAKE_ASSETS)))

    info = upd.fetch_latest_release()

    assert info.asset_name == "bilibili-submit-gui-portable.exe"


def test_missing_assets_leave_the_url_only(monkeypatch):
    """release 一个资产都没有（或被删过）时只带下载页，不崩。"""
    monkeypatch.setattr(upd, "release_form", lambda: "installed")
    _patch_session(monkeypatch, _FakeSession(_release("v0.2.7")))

    info = upd.fetch_latest_release()

    assert info.asset_url is None
    assert info.url == "https://example.test/release"


def test_assets_without_a_download_url_are_skipped(monkeypatch):
    """api_url 要带 token，browser_download_url 才是公开直链。"""
    broken = [{"name": "bilibili-submit-setup.exe", "api_url": "https://api/x"}]
    monkeypatch.setattr(upd, "release_form", lambda: "installed")
    _patch_session(monkeypatch, _FakeSession(_release("v0.2.7", assets=broken)))

    info = upd.fetch_latest_release()

    assert info.asset_url is None


def test_pick_asset_respects_priority_order():
    picked = upd.pick_asset(FAKE_ASSETS, ("bilibili-submit-cli.exe", "bilibili-submit-setup.exe"))

    assert picked["name"] == "bilibili-submit-cli.exe"


@pytest.mark.parametrize("assets", [None, "nope", 42, [None, "x", {}]])
def test_pick_asset_survives_junk(assets):
    assert upd.pick_asset(assets, ("bilibili-submit-setup.exe",)) is None


def test_pick_asset_returns_none_when_nothing_matches():
    assert upd.pick_asset(FAKE_ASSETS, ("bilibili-submit-setup-arm64.exe",)) is None


# ---------- 下载 ----------


def _downloading_release(**kw) -> upd.ReleaseInfo:
    base = dict(
        version="0.4.0",
        tag="v0.4.0",
        url="https://example.test/release",
        prerelease=False,
        asset_url="https://d/setup.exe",
        asset_name="bilibili-submit-setup.exe",
        asset_size=12,
    )
    base.update(kw)
    return upd.ReleaseInfo(**base)


def test_download_writes_the_file_and_cleans_the_part(tmp_path, monkeypatch):
    content = b"MZ" + b"x" * 10
    response = _FakeStreamResponse(
        content, headers={"Content-Length": str(len(content))}
    )
    _patch_session(monkeypatch, _FakeSession(response))
    info = _downloading_release(asset_size=len(content))

    out = upd.download_update(info, dest_dir=tmp_path)

    assert out == tmp_path / "bilibili-submit-setup.exe"
    assert out.read_bytes() == content
    assert not (tmp_path / "bilibili-submit-setup.exe.part").exists()


def test_download_reports_progress(tmp_path, monkeypatch):
    content = b"MZ" + b"x" * 300_000
    response = _FakeStreamResponse(
        content, headers={"Content-Length": str(len(content))}
    )
    _patch_session(monkeypatch, _FakeSession(response))
    seen = []
    info = _downloading_release(asset_size=len(content))

    upd.download_update(
        info, dest_dir=tmp_path, on_progress=lambda done, total: seen.append((done, total))
    )

    assert seen, "一次进度都没上报"
    assert [done for done, _total in seen] == sorted(done for done, _t in seen)
    assert seen[-1][0] == len(content)
    assert seen[-1][1] == len(content)


def test_download_streams_in_chunks(tmp_path, monkeypatch):
    """大文件不许整个读进内存——iter_content 是流式的。"""
    content = b"MZ" + b"y" * 200_000
    response = _FakeStreamResponse(
        content, headers={"Content-Length": str(len(content))}
    )
    _patch_session(monkeypatch, _FakeSession(response))
    info = _downloading_release(asset_size=len(content))

    out = upd.download_update(info, dest_dir=tmp_path, chunk_size=64 * 1024)

    assert out.stat().st_size == len(content)


def test_download_cancelled_midway_leaves_nothing_behind(tmp_path, monkeypatch):
    content = b"MZ" + b"z" * 200_000
    response = _FakeStreamResponse(
        content, headers={"Content-Length": str(len(content))}
    )
    _patch_session(monkeypatch, _FakeSession(response))
    info = _downloading_release(asset_size=len(content))
    calls = {"n": 0}

    def is_cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 2  # 吐完两个 chunk 再取消

    with pytest.raises(upd.UpdateCancelled):
        upd.download_update(
            info, dest_dir=tmp_path, is_cancelled=is_cancelled, chunk_size=64 * 1024
        )

    assert list(tmp_path.iterdir()) == [], "取消后不许留下 .part 或成品"


def test_download_without_asset_is_a_clear_error(tmp_path):
    info = upd.ReleaseInfo("0.4.0", "v0.4.0", "https://example.test/x", False)

    with pytest.raises(Exception) as excinfo:
        upd.download_update(info, dest_dir=tmp_path)

    assert "没有可下载" in str(excinfo.value)


def test_download_http_error_mentions_the_status(tmp_path, monkeypatch):
    _patch_session(
        monkeypatch, _FakeSession(_FakeStreamResponse(status_code=404))
    )

    with pytest.raises(Exception) as excinfo:
        upd.download_update(_downloading_release(), dest_dir=tmp_path)

    assert "404" in str(excinfo.value)


def test_download_network_failure_is_wrapped(tmp_path, monkeypatch):
    import requests

    _patch_session(monkeypatch, _FakeSession(exc=requests.ConnectionError("down")))

    with pytest.raises(Exception) as excinfo:
        upd.download_update(_downloading_release(), dest_dir=tmp_path)

    assert "网络不可达" in str(excinfo.value)


def test_download_short_file_is_rejected(tmp_path, monkeypatch):
    """字节数对不上就是没下完——留着它比没有更危险。"""
    content = b"MZ" + b"x" * 4
    response = _FakeStreamResponse(
        content, headers={"Content-Length": str(len(content))}
    )
    _patch_session(monkeypatch, _FakeSession(response))
    info = _downloading_release(asset_size=len(content) + 100)

    with pytest.raises(Exception) as excinfo:
        upd.download_update(info, dest_dir=tmp_path)

    assert "不完整" in str(excinfo.value)
    assert not (tmp_path / "bilibili-submit-setup.exe.part").exists()


def test_download_non_executable_is_rejected(tmp_path, monkeypatch):
    """大小对得上但内容不对（HTML 错误页、代理占位文件）。"""
    content = b"<html>nope</html>"
    response = _FakeStreamResponse(
        content, headers={"Content-Length": str(len(content))}
    )
    _patch_session(monkeypatch, _FakeSession(response))
    info = _downloading_release(asset_size=len(content))

    with pytest.raises(Exception) as excinfo:
        upd.download_update(info, dest_dir=tmp_path)

    assert "不是有效的程序文件" in str(excinfo.value)


def test_download_closes_the_session(tmp_path, monkeypatch):
    content = b"MZ" + b"x" * 4
    session = _FakeSession(
        _FakeStreamResponse(content, headers={"Content-Length": str(len(content))})
    )
    _patch_session(monkeypatch, session)
    info = _downloading_release(asset_size=len(content))

    upd.download_update(info, dest_dir=tmp_path)

    assert session.closed is True


def test_download_uses_the_release_asset_url(tmp_path, monkeypatch):
    content = b"MZ" + b"x" * 4
    session = _FakeSession(
        _FakeStreamResponse(content, headers={"Content-Length": str(len(content))})
    )
    _patch_session(monkeypatch, session)
    info = _downloading_release(asset_size=len(content))

    upd.download_update(info, dest_dir=tmp_path)

    assert session.last_url == "https://d/setup.exe"
    assert session.last_stream is True


# ---------- 自替换与清理 ----------


def _fake_portable(tmp_path, monkeypatch):
    """搭一个「正在运行的便携版」：tmp_path 下的 exe。"""
    exe = tmp_path / "bilibili-submit-gui-portable.exe"
    exe.write_bytes(b"OLD")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


def test_replace_executable_swaps_files_and_keeps_the_old(tmp_path, monkeypatch):
    current = _fake_portable(tmp_path, monkeypatch)
    local = tmp_path / "new.exe"
    local.write_bytes(b"NEW")

    old = upd.replace_executable(local)

    assert old == current.with_name("bilibili-submit-gui-portable.exe.old")
    assert old.read_bytes() == b"OLD"
    assert current.read_bytes() == b"NEW"
    assert not local.exists()


def test_replace_executable_refuses_source_runs(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    local = tmp_path / "new.exe"
    local.write_bytes(b"NEW")

    with pytest.raises(Exception) as excinfo:
        upd.replace_executable(local)

    assert "便携版" in str(excinfo.value)


def test_replace_executable_keeps_the_exe_when_the_dir_is_read_only(
    tmp_path, monkeypatch
):
    """换不动就算了，但不能把原来的 exe 弄丢。"""
    current = _fake_portable(tmp_path, monkeypatch)
    local = tmp_path / "new.exe"
    local.write_bytes(b"NEW")

    def boom(*_a, **_k):
        raise OSError("拒绝访问")

    monkeypatch.setattr(upd.shutil, "move", boom)

    with pytest.raises(Exception):
        upd.replace_executable(local)

    assert current.read_bytes() == b"OLD"
    assert not current.with_name("bilibili-submit-gui-portable.exe.old").exists()


def test_cleanup_removes_stale_upgrade_leftovers(tmp_path, monkeypatch):
    current = _fake_portable(tmp_path, monkeypatch)
    old = current.with_name("bilibili-submit-gui-portable.exe.old")
    new = current.with_name("bilibili-submit-gui-portable.exe.new")
    old.write_bytes(b"OLD")
    new.write_bytes(b"NEW")

    upd.cleanup_stale_updates()

    assert not old.exists()
    assert not new.exists()


def test_cleanup_does_nothing_when_not_frozen(tmp_path, monkeypatch):
    current = _fake_portable(tmp_path, monkeypatch)
    old = current.with_name("bilibili-submit-gui-portable.exe.old")
    old.write_bytes(b"OLD")
    monkeypatch.delattr(sys, "frozen", raising=False)

    upd.cleanup_stale_updates()

    assert old.exists(), "没在跑便携版就别动别人的文件"


def test_cleanup_survives_missing_files(tmp_path, monkeypatch):
    _fake_portable(tmp_path, monkeypatch)  # frozen，但旁边没有 .old/.new
    upd.cleanup_stale_updates()  # 不该抛


# ---------- 与打包配置的一致 ----------


def test_repo_matches_installer_iss():
    """仓库地址在两处各写了一遍：这里和 installer.iss 的 AppURL。

    改成不一样不会有任何编译错误，只有用户点了「检查更新」才发现
    跳去了别的地方（或者根本打不开）。
    """
    import re

    text = open(
        os.path.join(ROOT, "installer.iss"), encoding="utf-8-sig"
    ).read()
    match = re.search(r'#define\s+AppURL\s+"([^"]+)"', text)
    assert match, "installer.iss 里找不到 AppURL 定义"

    app_url = match.group(1)
    assert upd.RELEASES_URL.startswith(app_url), (
        f"RELEASES_URL={upd.RELEASES_URL} 与 installer.iss 的 AppURL={app_url} 不同源"
    )
    assert f"/{upd.REPO}" in app_url
    assert f"repos/{upd.REPO}/" in upd.API_URL


# ---------- 界面 ----------


from conftest import needs_display, run_until  # noqa: E402 - 由 tests/conftest.py 统一提供


def _build_view(root, monkeypatch):
    """建 App 与设置页，并把网络全部打桩。"""
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui import app as app_mod
    from bilibili_submit.ui import theme
    from bilibili_submit.ui.app import App
    from bilibili_submit.ui import environment as env_mod
    from bilibili_submit.ui.views import settings as settings_mod

    class _FakeFfmpeg:
        source = "system"
        path = "/usr/bin/ffmpeg"

    def probe(_f, **_k):
        return env_mod.EnvironmentSnapshot(
            logged_in=False, ffmpeg=_FakeFfmpeg(), ffmpeg_version=""
        )

    monkeypatch.setattr(app_mod, "probe_environment", probe)
    monkeypatch.setattr(settings_mod, "probe_environment", probe)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    theme.apply(style)
    app = App(root)
    app.pack(fill="both", expand=True)
    return app, app._views["设置"]


def _text(view) -> str:
    """更新那张 KeyValueList 当前显示的全部文本。

    ``_rows`` 里存的是控件而不是数据，所以得从 Label 上把文本取下来。
    """
    return "\n".join(value.cget("text") for _key, value in view._update._rows)


@needs_display
def test_manual_check_runs_off_the_main_thread(monkeypatch):
    """慢动作不许在主线程上——那会让整个界面冻住。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        seen = {}
        started = threading.Event()

        def fake_fetch(proxy=None, timeout=(5, 10)):
            seen["thread"] = threading.current_thread().ident
            started.set()
            time.sleep(0.2)
            return None

        monkeypatch.setattr(settings_mod, "fetch_latest_release", fake_fetch)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)

        view._on_check_update()
        assert started.wait(2), "检查根本没开始"

        run_until(root, lambda: "检查中" not in _text(view))
        assert seen["thread"] != threading.main_thread().ident
    finally:
        root.destroy()


@needs_display
def test_no_update_shows_in_place(monkeypatch):
    """已是最新：就地显示，不弹窗。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        # 返回**当前版本**：查到了，但不比本地新，这才是「已是最新」
        same = upd.ReleaseInfo(__version__, f"v{__version__}", "https://x", False)
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: same)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)
        popped = []
        monkeypatch.setattr(
            settings_mod.messagebox, "askyesno", lambda *a, **k: popped.append(1)
        )

        view._on_check_update()
        run_until(root, lambda: "检查中" not in _text(view))

        assert "已是最新" in _text(view)
        assert popped == []
    finally:
        root.destroy()


@needs_display
def test_manual_check_distinguishes_failure_from_latest(monkeypatch):
    """网络不通时要说「检查失败」，不能拿「已是最新」糊弄过去。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: None)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)

        view._on_check_update()
        run_until(root, lambda: "检查中" not in _text(view))

        assert "检查失败" in _text(view)
    finally:
        root.destroy()


@needs_display
def test_source_form_prompt_still_offers_the_download_page(monkeypatch):
    """源码形态没有「装」这回事，保留浏览器兜底这条路。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo("9.9.9", "v9.9.9", "https://example.test/x", False)
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)

        monkeypatch.setattr(settings_mod.messagebox, "askyesno", lambda *a, **k: True)
        opened = []
        monkeypatch.setattr(
            settings_mod, "open_release_page", lambda url: opened.append(url)
        )
        monkeypatch.setattr(settings_mod, "should_notify", lambda version, path=None: True)
        marked = []
        monkeypatch.setattr(
            settings_mod, "mark_version_seen", lambda v, path=None: marked.append(v)
        )

        view._on_check_update()
        run_until(root, lambda: bool(opened))

        assert opened == ["https://example.test/x"]
        assert marked == ["9.9.9"]
    finally:
        root.destroy()


@needs_display
def test_declining_the_prompt_does_not_open_the_browser(monkeypatch):
    """点「否」就是不想看，不该偷偷打开浏览器，也不该偷偷开始下载。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo(
            "9.9.9",
            "v9.9.9",
            "https://example.test/x",
            False,
            asset_url="https://d/gui-portable.exe",
            asset_name="bilibili-submit-gui-portable.exe",
        )
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)
        monkeypatch.setattr(
            settings_mod.messagebox, "askyesno", lambda *a, **k: False
        )
        opened = []
        monkeypatch.setattr(
            settings_mod, "open_release_page", lambda url: opened.append(url)
        )
        downloaded = []
        monkeypatch.setattr(
            settings_mod, "download_update", lambda *a, **k: downloaded.append(1)
        )
        monkeypatch.setattr(settings_mod, "should_notify", lambda version, path=None: True)
        monkeypatch.setattr(settings_mod, "mark_version_seen", lambda v, path=None: None)

        view._on_check_update()
        run_until(root, lambda: "9.9.9" in _text(view))

        assert opened == []
        assert downloaded == []
    finally:
        root.destroy()


def _wire_in_app_update(monkeypatch, settings_mod, view, tmp_path, *, form, info):
    """把下载与应用更新的每一步都打桩，返回各步的记录字典。"""
    seen = {"opened": [], "downloaded": [], "replaced": [], "relaunched": [],
            "installer": [], "dialogs": [], "quit": False}

    target = tmp_path / (info.asset_name or "setup.exe")
    target.write_bytes(b"MZfake")

    def fake_download(release, *, dest_dir=None, proxy=None,
                      on_progress=None, is_cancelled=None, chunk_size=64 * 1024):
        seen["downloaded"].append(release)
        if on_progress is not None:
            on_progress(1_000_000, release.asset_size or 72_000_000)
        return target

    monkeypatch.setattr(settings_mod, "release_form", lambda: form)
    monkeypatch.setattr(settings_mod, "download_update", fake_download)
    monkeypatch.setattr(settings_mod, "open_release_page", lambda url: seen["opened"].append(url))
    monkeypatch.setattr(
        settings_mod, "replace_executable", lambda local: seen["replaced"].append(local)
    )
    monkeypatch.setattr(
        settings_mod, "relaunch_app", lambda exe: seen["relaunched"].append(exe)
    )
    monkeypatch.setattr(
        settings_mod, "launch_installer", lambda local: seen["installer"].append(local)
    )
    monkeypatch.setattr(view, "_quit", lambda: seen.__setitem__("quit", True))
    monkeypatch.setattr(settings_mod.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(
        settings_mod.messagebox, "showinfo", lambda *a, **k: seen["dialogs"].append("info")
    )
    monkeypatch.setattr(
        settings_mod.messagebox, "showerror", lambda *a, **k: seen["dialogs"].append("error")
    )
    return seen


@needs_display
def test_new_version_updates_in_app_without_the_browser(monkeypatch, tmp_path):
    """核心回归：点「是」之后全程在客户端里完成，一次浏览器都不开。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo(
            "9.9.9",
            "v9.9.9",
            "https://example.test/x",
            False,
            asset_url="https://d/gui-portable.exe",
            asset_name="bilibili-submit-gui-portable.exe",
            asset_size=72_000_000,
        )
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)
        monkeypatch.setattr(settings_mod, "should_notify", lambda version, path=None: True)
        monkeypatch.setattr(settings_mod, "mark_version_seen", lambda v, path=None: None)
        seen = _wire_in_app_update(monkeypatch, settings_mod, view, tmp_path,
                                   form="portable", info=info)

        view._on_check_update()
        run_until(root, lambda: seen["quit"])

        assert seen["opened"] == [], "更新不该跳浏览器"
        assert [r.version for r in seen["downloaded"]] == ["9.9.9"]
        assert seen["replaced"] == [tmp_path / "bilibili-submit-gui-portable.exe"]
        assert seen["relaunched"], "便携版换完必须把新版拉起来"
        assert seen["installer"] == []
    finally:
        root.destroy()


@needs_display
def test_installed_form_runs_the_downloaded_installer(monkeypatch, tmp_path):
    """安装版：下载安装器 → 跑安装器 → 退出，不自己替换文件。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo(
            "9.9.9",
            "v9.9.9",
            "https://example.test/x",
            False,
            asset_url="https://d/setup.exe",
            asset_name="bilibili-submit-setup.exe",
            asset_size=33_000_000,
        )
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)
        monkeypatch.setattr(settings_mod, "should_notify", lambda version, path=None: True)
        monkeypatch.setattr(settings_mod, "mark_version_seen", lambda v, path=None: None)
        seen = _wire_in_app_update(monkeypatch, settings_mod, view, tmp_path,
                                   form="installed", info=info)

        view._on_check_update()
        run_until(root, lambda: seen["quit"])

        assert seen["opened"] == []
        assert seen["installer"] == [tmp_path / "bilibili-submit-setup.exe"]
        assert seen["replaced"] == [], "安装版不该自己替换文件"
        assert seen["relaunched"] == []
    finally:
        root.destroy()


@needs_display
def test_declining_the_ready_prompt_keeps_the_download(monkeypatch, tmp_path):
    """「更新就绪」时改主意：不替换、不退出，但告诉用户文件在哪儿。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo(
            "9.9.9",
            "v9.9.9",
            "https://example.test/x",
            False,
            asset_url="https://d/gui-portable.exe",
            asset_name="bilibili-submit-gui-portable.exe",
            asset_size=72_000_000,
        )
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)
        monkeypatch.setattr(settings_mod, "should_notify", lambda version, path=None: True)
        monkeypatch.setattr(settings_mod, "mark_version_seen", lambda v, path=None: None)
        seen = _wire_in_app_update(monkeypatch, settings_mod, view, tmp_path,
                                   form="portable", info=info)
        # 第一个对话框（要不要更新）点是，第二个（更新就绪）点否
        answers = iter([True, False])
        monkeypatch.setattr(
            settings_mod.messagebox, "askyesno", lambda *a, **k: next(answers)
        )

        view._on_check_update()
        run_until(root, lambda: "info" in seen["dialogs"])

        assert seen["replaced"] == []
        assert seen["relaunched"] == []
        assert seen["quit"] is False
    finally:
        root.destroy()


@needs_display
def test_download_cancel_is_quiet_and_resets_the_button(monkeypatch):
    """取消不是错误：不弹框，按钮恢复成可点的「检查更新」。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo(
            "9.9.9",
            "v9.9.9",
            "https://example.test/x",
            False,
            asset_url="https://d/gui-portable.exe",
            asset_name="bilibili-submit-gui-portable.exe",
            asset_size=72_000_000,
        )
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)
        monkeypatch.setattr(settings_mod, "should_notify", lambda version, path=None: True)
        monkeypatch.setattr(settings_mod, "mark_version_seen", lambda v, path=None: None)
        monkeypatch.setattr(settings_mod, "release_form", lambda: "portable")
        monkeypatch.setattr(settings_mod.messagebox, "askyesno", lambda *a, **k: True)
        monkeypatch.setattr(settings_mod.messagebox, "showinfo", lambda *a, **k: None)

        started = threading.Event()

        def blocking_download(release, *, dest_dir=None, proxy=None,
                              on_progress=None, is_cancelled=None, chunk_size=64 * 1024):
            started.set()
            while not is_cancelled():
                time.sleep(0.01)
            raise upd.UpdateCancelled("下载已取消")

        monkeypatch.setattr(settings_mod, "download_update", blocking_download)
        errors = []
        monkeypatch.setattr(
            settings_mod.messagebox, "showerror", lambda *a, **k: errors.append(1)
        )

        view._on_check_update()
        run_until(root, lambda: started.is_set())
        assert str(view._update_button.cget("text")) == "取消下载"

        view._on_cancel_download()
        run_until(root, lambda: str(view._update_button.cget("text")) == "检查更新")

        assert errors == [], "取消不该弹错误框"
        assert "下载已取消" in _text(view)
        assert "disabled" not in view._update_button.state()
    finally:
        root.destroy()


@needs_display
def test_download_failure_shows_the_reason(monkeypatch):
    """下载失败必须让人看见原因——它和「检查不到」不是一回事。"""
    import tkinter as tk

    from bilibili_submit.exceptions import BiliError
    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo(
            "9.9.9",
            "v9.9.9",
            "https://example.test/x",
            False,
            asset_url="https://d/setup.exe",
            asset_name="bilibili-submit-setup.exe",
        )
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)
        monkeypatch.setattr(settings_mod, "should_notify", lambda version, path=None: True)
        monkeypatch.setattr(settings_mod, "mark_version_seen", lambda v, path=None: None)
        monkeypatch.setattr(settings_mod, "release_form", lambda: "installed")
        monkeypatch.setattr(settings_mod.messagebox, "askyesno", lambda *a, **k: True)
        monkeypatch.setattr(settings_mod.messagebox, "showinfo", lambda *a, **k: None)

        def failing_download(release, **kw):
            raise BiliError("下载不完整", hint="网络问题，重试即可")

        monkeypatch.setattr(settings_mod, "download_update", failing_download)
        errors = []
        monkeypatch.setattr(
            settings_mod.messagebox,
            "showerror",
            lambda title, detail, **k: errors.append(detail),
        )

        view._on_check_update()
        run_until(root, lambda: bool(errors))

        assert "重试即可" in errors[0], "hint 要一起显示，只给标题等于没说"
        assert "下载失败" in _text(view)
        assert str(view._update_button.cget("text")) == "检查更新"
    finally:
        root.destroy()


@needs_display
def test_download_progress_reaches_the_list(monkeypatch, tmp_path):
    """进度要落到界面上——几十 MB 的下载没有任何反馈等于程序死了。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo(
            "9.9.9",
            "v9.9.9",
            "https://example.test/x",
            False,
            asset_url="https://d/gui-portable.exe",
            asset_name="bilibili-submit-gui-portable.exe",
            asset_size=72_000_000,
        )
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)
        monkeypatch.setattr(settings_mod, "should_notify", lambda version, path=None: True)
        monkeypatch.setattr(settings_mod, "mark_version_seen", lambda v, path=None: None)
        monkeypatch.setattr(settings_mod, "release_form", lambda: "portable")

        target = tmp_path / "bilibili-submit-gui-portable.exe"
        target.write_bytes(b"MZfake")

        def chunked_download(release, *, dest_dir=None, proxy=None,
                             on_progress=None, is_cancelled=None, chunk_size=64 * 1024):
            # 两次上报之间隔过节流窗口，保证第二条真的能落到界面上
            on_progress(1_000_000, release.asset_size)
            time.sleep(0.3)
            on_progress(50_000_000, release.asset_size)
            return target

        monkeypatch.setattr(settings_mod, "download_update", chunked_download)
        # 「更新就绪」点否：流程停在这里，进度行已经留下过
        answers = iter([True, False])
        monkeypatch.setattr(
            settings_mod.messagebox, "askyesno", lambda *a, **k: next(answers)
        )
        monkeypatch.setattr(settings_mod.messagebox, "showinfo", lambda *a, **k: None)

        view._on_check_update()
        run_until(root, lambda: "MB" in _text(view))

        # _text 只拼值不拼键，所以断言落在进度文字本身：
        # 总数来自 asset_size，说明进度用的是 release 报的真实大小
        assert "72.0 MB" in _text(view)
    finally:
        root.destroy()


@needs_display
def test_auto_check_goes_through_the_throttled_path(monkeypatch):
    """启动时的自动检查必须走限频那条路（``force=False``）。

    写成 ``force=True`` 界面上完全看不出差别——只是每次启动都去请求
    一次，撞上限频那天用户只会发现「这程序怎么一直查不到更新」。
    """
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        seen = {}
        monkeypatch.setattr(
            settings_mod,
            "check_for_update",
            lambda proxy=None, force=False, path=None: seen.setdefault("force", force),
        )
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: None)

        view.auto_check_update()
        run_until(root, lambda: "force" in seen)

        assert seen["force"] is False
    finally:
        root.destroy()
