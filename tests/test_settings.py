"""设置页与状态栏的异步环境自检测试。

这一页的验收点只有一个：**慢的东西不许跑在 Tk 主线程上**。
检测要读 cookie 文件、探 ffmpeg 路径、必要时启动子进程跑
``ffmpeg -version``；放在主线程，用户点开设置页看到的就是程序死了。

所以这里的用例大半在盯「线程 id」和「事件循环能不能等到结果」，
而不是盯显示文案——文案对了但跑在主线程上，一样是卡顿。
"""

import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.ui import environment as env_mod  # noqa: E402
from bilibili_submit.ui.views.settings import SettingsView  # noqa: E402


def _display_available() -> bool:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.destroy()
        return True
    except Exception:  # noqa: BLE001
        return False


needs_display = pytest.mark.skipif(
    not _display_available(), reason="无显示环境（CI 可配 Xvfb 后自动启用）"
)


def run_until(root, predicate, timeout_ms: int = 5000) -> None:
    """跑真实事件循环直到 ``predicate()`` 为真。

    ``update_idletasks()`` 只跑重绘不跑普通 ``after`` 回调——
    Worker 完成只是把结果排进队列，能不能排上、什么时候排上
    全看事件循环。所以异步 UI 的测试不能用它等。

    超时直接 fail：宁可红一次，也不要把「结果没到达」读成「功能正常」。
    """
    state = {"done": False}

    def poll():
        if predicate():
            state["done"] = True
            root.quit()
        else:
            root.after(10, poll)

    root.after(0, poll)
    root.after(timeout_ms, root.quit)
    root.mainloop()
    assert state["done"], f"等待异步结果超时（{timeout_ms}ms）"
    assert predicate()


class _FakeFfmpeg:
    """替身，只带界面用到的两个属性。"""

    source = "system"
    path = "/usr/bin/ffmpeg"


def _build(root, monkeypatch, probe=None):
    """建 App，返回 ``(app, settings_view)``。

    默认把探测打成立即返回，省得每个用例都去真读文件、真探 ffmpeg。
    """
    from tkinter import ttk

    from bilibili_submit.ui import app as app_mod
    from bilibili_submit.ui import theme
    from bilibili_submit.ui.app import App
    from bilibili_submit.ui.views import settings as settings_mod

    if probe is None:
        probe = lambda _f, **_k: env_mod.EnvironmentSnapshot(  # noqa: E731
            logged_in=False, ffmpeg=_FakeFfmpeg(), ffmpeg_version=""
        )
    monkeypatch.setattr(app_mod, "probe_environment", probe)
    monkeypatch.setattr(settings_mod, "probe_environment", probe)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except root.tk.TclError:
        pass
    theme.apply(style)
    app = App(root)
    app.pack(fill="both", expand=True)
    return app, app._views["设置"]


# ---------- 纯逻辑（不需要显示器） ----------


def test_probe_reads_cookie_exactly_once(tmp_path, monkeypatch):
    """一次检测里 ``load_cookies`` 只调一次。

    原来的写法是 ``theme.tone("ok") if ctx.logged_in else ...`` 加
    ``text="…" if ctx.logged_in else "…"``，那个 property 在同一行里
    被求值两遍——cookie 文件读了两遍。反向验证时把它改回去，这条必须红。
    """
    from bilibili_submit.ui import environment as env_mod

    calls = []

    def fake_load(path):
        calls.append(path)
        return {"SESSDATA": "x", "bili_jct": "y", "DedeUserID": "1"}

    monkeypatch.setattr(env_mod, "load_cookies", fake_load)

    snap = env_mod.probe_environment(str(tmp_path / "c.json"))

    assert len(calls) == 1, f"cookie 文件读了几次？{calls}"
    assert snap.logged_in is True
    assert snap.login_error == ""


def test_probe_reports_cookie_error(tmp_path, monkeypatch):
    """cookie 读不了要记原因，不能吞掉只说「未登录」。

    「文件不存在」和「文件坏了」都是未登录，但用户的下一步动作
    完全不同：前者去登录，后者得找出是什么程序改坏了这个文件。
    """
    from bilibili_submit.exceptions import BiliError
    from bilibili_submit.ui import environment as env_mod

    def boom(_path):
        raise BiliError("Cookie 文件读取失败: 权限不足")

    monkeypatch.setattr(env_mod, "load_cookies", boom)

    snap = env_mod.probe_environment(str(tmp_path / "c.json"))

    assert snap.logged_in is False
    assert "权限不足" in snap.login_error


def test_probe_can_skip_version_for_statusbar(tmp_path, monkeypatch):
    """状态栏只要知道 ffmpeg 在不在，不该去起子进程读版本。"""
    from bilibili_submit.ui import environment as env_mod

    seen = []

    def spy(info):
        seen.append(info)
        return "7.1"

    monkeypatch.setattr(env_mod, "ffmpeg_version", spy)

    env_mod.probe_environment(str(tmp_path / "c.json"))
    assert seen == [], "状态栏不该读 ffmpeg 版本——那一步要启动子进程"

    env_mod.probe_environment(str(tmp_path / "c.json"), include_ffmpeg_version=True)
    assert len(seen) == 1


def test_probe_handles_missing_ffmpeg(tmp_path, monkeypatch):
    from bilibili_submit.ui import environment as env_mod

    monkeypatch.setattr(env_mod, "load_cookies", lambda _p: {})
    monkeypatch.setattr(env_mod, "ffmpeg_status", lambda: None)

    snap = env_mod.probe_environment(str(tmp_path / "c.json"), include_ffmpeg_version=True)
    assert snap.ffmpeg is None
    assert snap.ffmpeg_version == ""


# ---------- 需要 Tk ----------


@needs_display
def test_probe_runs_off_main_thread(monkeypatch):
    """**这条是卡顿修复的正身**：探测必须离开 Tk 主线程。

    探测函数被换成会阻塞的版本：它先记下自己的线程 id，再等一个闸门。
    只要 ``refresh()`` 能立刻返回、界面显示「检测中」，就说明探测在后台；
    反过来同步调的话，第一个断言就会卡死在闸门上直到超时。
    """
    import tkinter as tk

    main_thread = threading.get_ident()
    probe_threads: list[int] = []
    gate = threading.Event()
    release = threading.Event()

    def slow_probe(_cookie_file, **_kwargs):
        probe_threads.append(threading.get_ident())
        gate.set()
        release.wait(10)
        return env_mod.EnvironmentSnapshot(
            logged_in=True, ffmpeg=_FakeFfmpeg(), ffmpeg_version="6.0.1"
        )

    root = tk.Tk()
    try:
        _app, view = _build(root, monkeypatch, probe=slow_probe)
        view.refresh()  # 必须立刻返回，不能被闸门卡住

        assert gate.wait(5), "探测线程根本没起来"
        assert "检测中" in view._env.text(), (
            f"探测未完成时该显示过渡态，实际 {view._env.text()!r}"
        )
        assert probe_threads and probe_threads[0] != main_thread, (
            "探测跑在主线程上了——这正是要修的卡顿"
        )

        release.set()
        run_until(root, lambda: "已登录" in view._env.text())
        assert "6.0.1" in view._env.text()
        assert "检测中" not in view._env.text()
    finally:
        release.set()
        root.destroy()


@needs_display
def test_probe_runs_off_main_thread_in_statusbar(monkeypatch):
    """状态栏刷新也不能在主线程做 I/O。

    它有 4 个调用方（启动、换 cookie 路径、登录成功、批量页发现
    登录失效），每个都在主线程——只修设置页不够。
    """
    import tkinter as tk

    main_thread = threading.get_ident()
    probe_threads: list[int] = []
    gate = threading.Event()
    release = threading.Event()

    def slow_probe(_cookie_file, **_kwargs):
        probe_threads.append(threading.get_ident())
        gate.set()
        release.wait(10)
        return env_mod.EnvironmentSnapshot(logged_in=True, ffmpeg=_FakeFfmpeg())

    root = tk.Tk()
    try:
        app, _view = _build(root, monkeypatch, probe=slow_probe)
        app.refresh_status()

        assert gate.wait(5), "状态栏探测线程没起来"
        assert "检测中" in app._status_login.cget("text"), (
            f"状态栏该显示检测中，实际 {app._status_login.cget('text')!r}"
        )
        assert probe_threads[0] != main_thread

        release.set()
        run_until(root, lambda: "已登录" in app._status_login.cget("text"))
        assert "ffmpeg 就绪" in app._status_ffmpeg.cget("text")
    finally:
        release.set()
        root.destroy()


@needs_display
def test_refresh_returns_before_probe_finishes(monkeypatch):
    """``refresh()`` 本身必须是廉价的纯内存操作。

    挡的是「把 probe 同步塞进 refresh」这种改法——那样它就是上一次
    那个卡顿本身，只是提前了一个测试发现。
    """
    import time
    import tkinter as tk

    def slow_probe(_cookie_file, **_kwargs):
        time.sleep(0.3)
        return env_mod.EnvironmentSnapshot(logged_in=False, ffmpeg=None)

    root = tk.Tk()
    try:
        _app, view = _build(root, monkeypatch, probe=slow_probe)
        started = time.monotonic()
        view.refresh()
        elapsed = time.monotonic() - started

        assert elapsed < 0.15, f"refresh 同步等探测了 {elapsed:.3f}s"
        run_until(root, lambda: "检测中" not in view._env.text())
    finally:
        root.destroy()


@needs_display
def test_form_fills_from_context(monkeypatch):
    """表单要按当前设置回填，且只回填一次。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        app, view = _build(root, monkeypatch)
        app.ctx.proxy = "http://127.0.0.1:7890"
        app.ctx.cookie_file = "/tmp/cookie.json"

        view.refresh()
        assert view._proxy_var.get() == "http://127.0.0.1:7890"
        assert view._cookie_var.get() == "/tmp/cookie.json"

        # 用户改过之后，切页不能再覆盖回去
        view._proxy_var.set("http://192.168.1.1:1080")
        view.refresh()
        assert view._proxy_var.get() == "http://192.168.1.1:1080"
        assert app.ctx.proxy == "http://192.168.1.1:1080", "改动应即时写入 context"
    finally:
        root.destroy()


@needs_display
def test_proxy_change_writes_context(monkeypatch):
    import tkinter as tk

    root = tk.Tk()
    try:
        app, view = _build(root, monkeypatch)
        app.ctx.proxy = None
        view._proxy_var.set("http://127.0.0.1:7890")
        assert app.ctx.proxy == "http://127.0.0.1:7890"

        view._proxy_var.set("   ")
        assert app.ctx.proxy is None, "全空应写回 None，而不是空串"
    finally:
        root.destroy()


@needs_display
def test_env_shows_config_error(monkeypatch):
    """cookie 读失败要在自检里显示原因。"""
    import tkinter as tk

    def probe(_cookie_file, **_kwargs):
        return env_mod.EnvironmentSnapshot(
            logged_in=False, login_error="Cookie 文件读取失败: 权限不足", ffmpeg=None
        )

    root = tk.Tk()
    try:
        _app, view = _build(root, monkeypatch, probe=probe)
        view.refresh()

        run_until(root, lambda: "读取失败" in view._env.text())
        assert "权限不足" in view._env.text()
        assert "ffmpeg" in view._env.text(), "ffmpeg 那行要照常显示"
    finally:
        root.destroy()


@needs_display
def test_env_shows_missing_ffmpeg(monkeypatch):
    import tkinter as tk

    def probe(_cookie_file, **_kwargs):
        return env_mod.EnvironmentSnapshot(logged_in=True, ffmpeg=None)

    root = tk.Tk()
    try:
        _app, view = _build(root, monkeypatch, probe=probe)
        view.refresh()

        run_until(root, lambda: "未找到" in view._env.text())
        assert "仅影响自动抽帧" in view._env.text()
    finally:
        root.destroy()


@needs_display
def test_env_survives_unknown_version(monkeypatch):
    """读不到版本号也不能丢了「来源」和整行显示。"""
    import tkinter as tk

    def probe(_cookie_file, **_kwargs):
        return env_mod.EnvironmentSnapshot(
            logged_in=True, ffmpeg=_FakeFfmpeg(), ffmpeg_version="未知"
        )

    root = tk.Tk()
    try:
        _app, view = _build(root, monkeypatch, probe=probe)
        view.refresh()

        run_until(root, lambda: "system" in view._env.text())
        text = view._env.text()
        assert "未知" in text
    finally:
        root.destroy()


@needs_display
def test_env_has_no_config_row(monkeypatch):
    """环境自检不再有「配置文件」一行。

    客户端批量任务已改为选视频文件夹，用户不需要准备 config.yaml；
    这一行留着只会让人以为「不加载配置就不算装好」。
    """
    import tkinter as tk

    root = tk.Tk()
    try:
        _app, view = _build(root, monkeypatch)
        view.refresh()
        run_until(root, lambda: "检测中" not in view._env.text())

        text = view._env.text()
        assert "配置文件" not in text, f"不该再有「配置文件」一行：{text!r}"
        keys = [k.rstrip("：") for k, _v in view._env.rows()]
        assert [k.split("（")[0] for k in keys] == ["登录态", "代理", "ffmpeg"], keys
    finally:
        root.destroy()


@needs_display
def test_env_probe_failure_shows_error(monkeypatch):
    """探测抛异常也要显示，不能永远停在「检测中…」。"""
    import tkinter as tk

    def boom(*_a, **_k):
        raise RuntimeError("磁盘炸了")

    root = tk.Tk()
    try:
        _app, view = _build(root, monkeypatch, probe=boom)
        view.refresh()

        run_until(root, lambda: "检测失败" in view._env.text())
        assert "磁盘炸了" in view._env.text()
    finally:
        root.destroy()


@needs_display
def test_statusbar_probe_failure_shows_error(monkeypatch):
    import tkinter as tk

    def boom(*_a, **_k):
        raise RuntimeError("权限不足")

    root = tk.Tk()
    try:
        app, _view = _build(root, monkeypatch, probe=boom)
        app.refresh_status()

        run_until(root, lambda: "检测失败" in app._status_ffmpeg.cget("text"))
        assert "权限不足" in app._status_ffmpeg.cget("text")
        assert "检测中" not in app._status_login.cget("text")
    finally:
        root.destroy()


@needs_display
def test_stale_probe_result_does_not_overwrite(monkeypatch):
    """晚到的旧探测结果不能覆盖新结果。

    构造真实的竞态：第一次探测（旧 cookie 路径）被卡住，第二次
    （新路径）先返回，最后放掉第一次。少了代号比对的话，界面会显示
    旧路径的「未登录」——而用户明明刚换到有登录态的文件。
    """
    import tkinter as tk

    first_started = threading.Event()
    release_first = threading.Event()
    calls = {"n": 0}
    #: 旧探测的 on_done 落地计数。必须确认它**执行过**，
    #: 否则「没被覆盖」可能只是因为它压根还没到——那这条用例就是白绿。
    stale_arrived = {"n": 0}

    def probe(cookie_file, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            first_started.set()
            release_first.wait(10)
            return env_mod.EnvironmentSnapshot(
                logged_in=False, ffmpeg=_FakeFfmpeg()
            )
        return env_mod.EnvironmentSnapshot(logged_in=True, ffmpeg=_FakeFfmpeg())

    root = tk.Tk()
    try:
        app, _view = _build(root, monkeypatch, probe=probe)

        # 第一次：旧路径，卡住
        app.ctx.cookie_file = "/old/cookie.json"
        app.refresh_status()
        assert first_started.wait(5), "第一次探测没起来"

        # 记下旧探测要用的代号，等它回来时确认它确实被调用了
        stale_generation = app._status_generation

        # 第二次：新路径，先返回
        app.ctx.cookie_file = "/new/cookie.json"
        app.refresh_status()
        run_until(root, lambda: "已登录" in app._status_login.cget("text"))
        assert app._status_generation > stale_generation, "第二次刷新应推进代号"

        # 盯住「旧代号有没有被试图应用」
        real_apply = app._apply_status

        def spy(generation, snapshot, _real=real_apply):
            if generation == stale_generation and not snapshot.logged_in:
                stale_arrived["n"] += 1
            return _real(generation, snapshot)

        app._apply_status = spy

        # 最后放掉第一次，让它晚到
        release_first.set()

        # 必须用真事件循环等它落地：Worker 完成只是把结果
        # after(0) 排进队列，update() 不处理普通 after 回调
        run_until(root, lambda: stale_arrived["n"] > 0, timeout_ms=5000)

        assert "已登录" in app._status_login.cget("text"), (
            f"旧结果覆盖了新的，实际 {app._status_login.cget('text')!r}"
        )
    finally:
        release_first.set()
        root.destroy()


@needs_display
def test_probe_thread_exception_is_delivered(monkeypatch):
    """探测在**工作线程**里抛异常时，``on_error`` 必须真的落地。

    两个容易混过去的点：

    - 线程抛未捕获异常后照样退出，所以只断言「线程结束了」抓不住它——
      真正的信号是异常有没有走 ``on_error``。
    - 异常从**工作线程**漏出去，整条线程就死：``on_done`` 和
      ``on_error`` 都收不到，界面永远停在「检测中…」。
    """
    import tkinter as tk

    original = threading.excepthook
    uncaught: list[BaseException] = []
    threading.excepthook = lambda args: uncaught.append(args.exc_value)
    try:
        def boom(*_a, **_k):
            raise RuntimeError("探测线程里炸了")

        root = tk.Tk()
        try:
            _app, view = _build(root, monkeypatch, probe=boom)
            view.refresh()

            run_until(root, lambda: "检测失败" in view._env.text())
            assert "探测线程里炸了" in view._env.text()
            # 异常必须被 on_error 接住，不该从线程里漏出去
            assert uncaught == [], f"异常漏出了工作线程：{uncaught}"
        finally:
            root.destroy()
    finally:
        threading.excepthook = original


@needs_display
def test_settings_view_is_not_config_driven(monkeypatch):
    """``SettingsView`` 不再引用任何配置文件概念。

    留着就说明「客户端要先有配置文件」这个心智模型没改干净。
    """
    import tkinter as tk

    root = tk.Tk()
    try:
        app, view = _build(root, monkeypatch)
        assert isinstance(view, SettingsView)
        assert not hasattr(app.ctx, "config_path"), (
            "AppContext.config_path 该删了——批量任务不再走配置文件"
        )
    finally:
        root.destroy()
