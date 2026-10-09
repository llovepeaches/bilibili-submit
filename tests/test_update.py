"""检查更新的测试。

**全程离线**：一次真的 HTTP 请求都不发。GitHub 那点限额（未认证
60 次/小时/IP）经不起测试反复打，而且联网测试会变成随机失败。

重点盯三类东西：

1. 版本比较的**数值**语义——按字符串比会得出 ``0.2.10 < 0.2.6``，
   第十个修订版反而提示不出来；
2. 各种失败都要**静默**——403 限流、404 没发过版、断网、响应不是
   JSON，都得返回 ``None`` 而不是抛异常；
3. 限频真的挡住了请求——不是「看起来查了」。
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


class _FakeSession:
    def __init__(self, response=None, exc=None):
        self.headers = {}
        self.proxies = {}
        self.closed = False
        self.last_url = None
        self.last_timeout = None
        self._response = response
        self._exc = exc

    def get(self, url, timeout=None):
        self.last_url = url
        self.last_timeout = timeout
        if self._exc is not None:
            raise self._exc
        return self._response

    def close(self):
        self.closed = True


def _patch_session(monkeypatch, session):
    monkeypatch.setattr(upd, "_new_session", lambda proxy=None: session)


def _release(tag="v9.9.9", url="https://example.test/release", prerelease=False):
    return _FakeResponse(
        payload={"tag_name": tag, "html_url": url, "prerelease": prerelease}
    )


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


def test_hint_mentions_program_files_when_installed(monkeypatch):
    monkeypatch.setattr(upd, "release_form", lambda: "installed")
    info = upd.ReleaseInfo("0.2.7", "v0.2.7", "https://example.test", False)

    title, body = upd.hint_text(info)

    assert title == "发现新版本 0.2.7"
    assert "Program Files" in body
    assert __version__ in body


def test_hint_mentions_replacing_the_exe_when_portable(monkeypatch):
    monkeypatch.setattr(upd, "release_form", lambda: "portable")
    info = upd.ReleaseInfo("0.2.7", "v0.2.7", "https://example.test", False)

    _title, body = upd.hint_text(info)

    assert "覆盖旧文件" in body
    assert "Program Files" not in body


def test_hint_mentions_pip_when_running_from_source(monkeypatch):
    monkeypatch.setattr(upd, "release_form", lambda: "source")
    info = upd.ReleaseInfo("0.2.7", "v0.2.7", "https://example.test", False)

    _title, body = upd.hint_text(info)

    assert "pip" in body


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
def test_new_version_prompts_and_opens_the_download_page(monkeypatch, tmp_path):
    """发现新版本：弹一次，点「是」才打开浏览器。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo("9.9.9", "v9.9.9", "https://example.test/x", False)
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)

        answers = {"value": True}
        monkeypatch.setattr(
            settings_mod.messagebox, "askyesno", lambda *a, **k: answers["value"]
        )
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
    """点「否」就是不想看，不该偷偷打开浏览器。"""
    import tkinter as tk

    from bilibili_submit.ui.views import settings as settings_mod

    root = tk.Tk()
    try:
        _app, view = _build_view(root, monkeypatch)
        info = upd.ReleaseInfo("9.9.9", "v9.9.9", "https://example.test/x", False)
        monkeypatch.setattr(settings_mod, "fetch_latest_release", lambda **_k: info)
        monkeypatch.setattr(settings_mod, "check_for_update", lambda **_k: None)
        monkeypatch.setattr(
            settings_mod.messagebox, "askyesno", lambda *a, **k: False
        )
        opened = []
        monkeypatch.setattr(
            settings_mod, "open_release_page", lambda url: opened.append(url)
        )
        monkeypatch.setattr(settings_mod, "should_notify", lambda version, path=None: True)
        monkeypatch.setattr(settings_mod, "mark_version_seen", lambda v, path=None: None)

        view._on_check_update()
        run_until(root, lambda: "9.9.9" in _text(view))

        assert opened == []
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
