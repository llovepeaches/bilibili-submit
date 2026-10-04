"""批量任务页测试。

这一页是 UI 里业务逻辑最重的一块：勾选、文件预检、状态着色、
取消归位，彼此还有联动（比如「只选失败项」要排除缺失项）。
所以拆成两类：

1. **纯逻辑**（默认跑）：截断、语义色映射、Event 结构——不需要显示器
2. **需要 Tk**（无 display 时跳过）：真的建窗口、扫描文件夹、点列表

第 2 类用一个临时视频目录，其中**故意放一个扫描后删掉的文件**，
用来验证预检——这是本页最容易出错也最有用的功能。

.. important::
   凡是走 Worker 的测试都靠 :func:`run_until` 等**真实事件循环**。
   ``update_idletasks()`` 只处理重绘，不处理普通 ``after()`` 回调：
   Worker 结束 ≠ Tk 已经执行了结果回调，用它等待会得到「列表还没填上」
   的假失败，或者更糟——压根没等，断言了一个还没发生的状态。
"""

import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.ui.views.tasks import (  # noqa: E402
    GROUP_FOLDER,
    GROUP_OFF,
    GROUP_OPTIONS,
    GROUP_PREFIX,
    PICKED,
    UNPICKED,
    SharedSubmitValues,
    _truncate,
)
from bilibili_submit.ui.workers import Event  # noqa: E402


# ---------- 纯逻辑 ----------


def test_truncate_keeps_short_text():
    assert _truncate("成功") == "成功"
    assert _truncate("", 18) == ""


def test_truncate_cuts_long_text_from_head():
    """从头截断并加省略号——错误原因的开头通常更有信息量。"""
    out = _truncate("601 投稿过于频繁，等待 30 分钟后重试")
    assert out.endswith("…")
    assert len(out) <= 18
    assert out.startswith("601")


def test_truncate_respects_custom_limit():
    assert len(_truncate("abcdefghij", 5)) == 5


def test_event_flags():
    """``succeeded`` 只看有没有 error，不看 status 文案。"""
    assert Event("done", status="成功").succeeded
    assert not Event("done", status="失败：x", error="x").succeeded
    assert not Event("start").done
    assert Event("done").done


def test_event_percent_uses_position_not_index():
    """进度百分比只能由 ``position`` 算。

    这是个真实踩过的坑：用户只勾了下标 3 和 5 的两项，此时
    ``total=2`` 而 ``index`` 是 3 和 5——拿 ``index + 1`` 当完成数会算出
    200% 和 300%，进度条第一项就顶满，状态栏还会显示「6/2」。
    """
    assert Event("done", index=3, position=1, total=2).percent == pytest.approx(50)
    assert Event("done", index=5, position=2, total=2).percent == pytest.approx(100)
    # position 没给时宁可算 0，也不拿 index 顶替——错误的百分比比没有更糟
    assert Event("done", index=3, total=2).percent == 0.0
    assert Event("done", position=1, total=0).percent == 0.0


def test_tones_cover_task_states():
    """任务页用到的每种状态都得有对应的语义色，否则会退回默认的灰。"""
    from bilibili_submit.ui import theme

    for name in ("idle", "busy", "ok", "error", "missing"):
        assert name in theme.TONES, f"{name} 缺少语义色"
        fg, bg = theme.tone(name)
        assert fg.startswith("#") and bg.startswith("#")


def test_unknown_tone_falls_back_to_idle():
    from bilibili_submit.ui import theme

    assert theme.tone("不存在的状态") == theme.TONES["idle"]


def test_shared_values_apply_clears_absolute_dtime():
    """统一参数填的是「距今几小时」，逐任务的绝对时间必须一并清掉。

    留着的话 resolve_dtime 会拿到一个上次执行的旧时间点，
    定时投稿会定到过去——而界面上根本没提示。
    """
    from bilibili_submit.config import TaskConfig

    task = TaskConfig(name="A", file="/x/a.mp4", dtime=1700000000, tid=1, tag="旧")
    shared = SharedSubmitValues(tid=138, tag="新,标签", desc="简介", dtime_offset_hours=6.0)

    out = shared.apply(task)

    assert out.tid == 138
    assert out.tag == "新,标签"
    assert out.desc == "简介"
    assert out.dtime_offset_hours == 6.0
    assert out.dtime is None
    # 原对象不能被就地改掉：执行线程可能正拿着它读
    assert task.tid == 1 and task.tag == "旧"


def test_shared_values_blank_tag_becomes_none():
    """空标签转成 None，而不是空串——空串会被当成「显式清空标签」。"""
    from bilibili_submit.config import TaskConfig

    out = SharedSubmitValues(tid=21).apply(TaskConfig(name="A", file="/x/a.mp4"))
    assert out.tag is None
    assert out.desc is None
    assert out.dtime_offset_hours is None


# ---------- 需要 Tk ----------


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

    为什么不用 ``update_idletasks()``：那只跑重绘，**不跑普通 ``after``
    回调**。Worker 完成只是把结果 ``after(0)`` 排进队列，能不能排上、
    什么时候排上都由事件循环说了算。

    超时直接 fail 而不是静默通过——宁可红一次，也不要让「异步结果没到达」
    被读成「功能正常」。
    """
    state = {"done": False}

    def poll():
        if predicate():
            state["done"] = True
            root.quit()
        else:
            root.after(10, poll)

    def on_timeout():
        root.quit()

    root.after(0, poll)
    root.after(timeout_ms, on_timeout)
    root.mainloop()
    assert state["done"], f"等待异步结果超时（{timeout_ms}ms）"
    assert predicate()


def _build(root, tmp_path=None, monkeypatch=None):
    """建 App 并返回批量任务页视图。

    ``tmp_path`` 给定时，偏好文件会指向临时目录、状态探测会被打桩，
    测试绝不碰用户真实的 ~/.config。
    """
    from tkinter import ttk

    from bilibili_submit.ui import theme
    from bilibili_submit.ui.app import App

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except root.tk.TclError:
        pass
    theme.apply(style)
    app = App(root)
    app.pack(fill="both", expand=True)
    view = app._views["批量任务"]
    if tmp_path is not None:
        view._state_file = tmp_path / "ui-state.json"
    if monkeypatch is not None:
        _stub_probe(monkeypatch, app)
    return app, view


def _stub_probe(monkeypatch, app) -> None:
    """把环境探测打桩成立即返回，省掉每次读文件 + 探 ffmpeg 路径。"""
    from bilibili_submit.ui import environment as env_mod

    class _Info:
        source = "system"
        path = "ffmpeg"

    snap = env_mod.EnvironmentSnapshot(logged_in=False, ffmpeg=_Info(), ffmpeg_version="")
    calls: list[tuple[str, bool]] = []

    def fake(cookie_file, *, include_ffmpeg_version=False):
        calls.append((cookie_file, include_ffmpeg_version))
        return snap

    monkeypatch.setattr(environment_probe_targets()[0], "probe_environment", fake)
    monkeypatch.setattr(environment_probe_targets()[1], "probe_environment", fake)


def environment_probe_targets() -> tuple[object, object]:
    """探测函数被打桩的位置（app 与 settings 各自导入了一份）。"""
    from bilibili_submit.ui import app as app_mod
    from bilibili_submit.ui.views import settings as settings_mod

    return app_mod, settings_mod


def _make_video(directory: Path, name: str) -> Path:
    video = directory / name
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"\x00" * 1024)
    return video


def _load_folder(root, view, directory, monkeypatch=None) -> None:
    """走文件夹来源加载，等真实结果落地。"""
    if monkeypatch is not None:
        _stub_probe(monkeypatch, view.app)
    view._dir_var.set(str(directory))
    view._load()
    run_until(root, lambda: not view._busy)


def _write_yaml(directory: Path, entries: list[tuple[str, str]]) -> str:
    """写一份 yaml 配置；``entries`` 是 (任务名, 文件路径) 列表。"""
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        "defaults:",
        "  tid: 21",
        "  tag: \"来自yaml的标签\"",
        "submit:",
        "  backend: web",
        "tasks:",
    ]
    for name, file_path in entries:
        lines.append(f"  - name: \"{name}\"")
        lines.append(f"    file: {file_path}")
        lines.append(f"    title: \"{name}\"")
    path = directory / "config.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _load_yaml(root, view, path) -> None:
    """走高级 yaml 入口加载，等真实结果落地。"""
    view._pick_config_from = None  # 标记不是文件对话框路径
    view._dir_var.set(str(path))
    from bilibili_submit.config import expand_tasks, load_config

    def loader(_report, is_cancelled):
        cfg = load_config(Path(path))
        return cfg, expand_tasks(cfg)

    view._start_load("读配置…", loader, mode="yaml", label=Path(path).name)
    run_until(root, lambda: not view._busy)


@needs_display
def test_folder_scan_needs_no_yaml(tmp_path, monkeypatch):
    """**客户端批量任务不用配置文件**——这条用例是那句话的守卫。

    把 ``load_config`` 打桩成一调就抛，再走文件夹加载：还能成功出列表，
    就证明主路径确实没碰 yaml。反向验证时把它改回 ``load_config``，
    这条必须变红。
    """
    import tkinter as tk

    from bilibili_submit.ui.views import tasks as tasks_mod

    _make_video(tmp_path, "a.mp4")
    _make_video(tmp_path, "b.mkv")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    def boom(*_a, **_k):
        raise AssertionError("文件夹路径不该碰 load_config")

    monkeypatch.setattr(tasks_mod, "load_config", boom)

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        assert [Path(t.file).name for t in view._tasks] == ["a.mp4", "b.mkv"]
        assert len(view._tree.get_children()) == 2
    finally:
        root.destroy()


@needs_display
def test_folder_tasks_use_filename_stem(tmp_path, monkeypatch):
    """文件夹模式下任务名和标题都取文件名 stem，用户少填一次。"""
    import tkinter as tk

    _make_video(tmp_path, "第一集.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        task = view._tasks[0]
        assert task.name == "第一集"
        assert task.title == "第一集"
        assert task.type == "single"
        assert view._tree.item("0", "values")[1] == "第一集"
        assert view._tree.item("0", "values")[0] == PICKED, "正常任务默认勾选"
        assert view._tree.item("0", "tags") == ("idle",)
    finally:
        root.destroy()


@needs_display
def test_shared_top_params_applied_to_every_task(tmp_path, monkeypatch):
    """顶部统一参数要落到**每一个**任务上，逐项断言。

    不做「最后把所有任务汇总比较一次」——那样某一项漏了也可能被
    其他项的正确值掩盖住，看着像全对。
    """
    import tkinter as tk

    for name in ("a.mp4", "b.mp4", "c.mkv"):
        _make_video(tmp_path, name)

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        view._tid_var.set("17 - 单机游戏")
        view._tag_var.set("日常,记录")
        view._desc_var.set("统一简介")
        view._dtime_var.set("6")

        cfg, selected = view._snapshot_run(view._selected_indexes())

        assert len(selected) == 3
        for index, task in selected:
            assert task.tid == 17, f"下标 {index} 的分区没跟上"
            assert task.tag == "日常,记录", f"下标 {index} 的标签没跟上"
            assert task.desc == "统一简介", f"下标 {index} 的简介没跟上"
            assert task.dtime_offset_hours == 6.0, f"下标 {index} 的延时没跟上"
        # 标题仍各是各的文件名，不能被统一参数抹平
        assert [Path(t.file).stem for _, t in selected] == ["a", "b", "c"]
        assert cfg.submit.backend == "web"
    finally:
        root.destroy()


@needs_display
def test_snapshot_does_not_mutate_task_list(tmp_path, monkeypatch):
    """快照不能就地改 ``self._tasks``。

    执行线程正拿着那些对象读，就地改会读到改了一半的字段。
    """
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        view._tid_var.set("17 - 单机游戏")
        before = list(view._tasks)
        view._snapshot_run([0])

        assert view._tasks == before, "任务列表不该被快照过程改掉"
        assert before[0].tid != 17
    finally:
        root.destroy()


@needs_display
def test_bad_dtime_rejected_before_worker_starts(tmp_path, monkeypatch):
    """延时填了非数字，要在**起线程之前**就报错。

    报错太晚会走 ``_on_error`` 那条路，虽然也能解锁，但用户已经看到
    界面闪了一圈「进行中」，而且白白起了一个线程。
    """
    import tkinter as tk

    from bilibili_submit.ui.views import tasks as tasks_mod

    _make_video(tmp_path, "a.mp4")

    root = tk.Tk()
    try:
        app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        class _Ctx:
            logged_in = True

            def client(self, need_login=True):
                return object()

        app.ctx = _Ctx()
        view._dtime_var.set("六")

        started = []
        monkeypatch.setattr(
            tasks_mod.Worker, "run", lambda self, *a, **k: started.append(a)
        )
        view._log.clear()
        view._run()

        assert started == [], "参数非法时不该启动任何线程"
        assert "不是数字" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_missing_file_marked_and_unpickable(tmp_path, monkeypatch):
    """扫描之后文件被删掉的行要标缺失、默认不勾、且勾不上。"""
    import tkinter as tk

    real = _make_video(tmp_path, "a_real.mp4")
    doomed = _make_video(tmp_path, "b_gone.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        assert sorted(view._picked) == ["0", "1"]

        # 扫描完成后再删——模拟用户自己把文件挪走了
        doomed.unlink()

        view._reset_state()
        view._fill_tree(view._tasks)

        assert view._missing == {"1"}, f"应只把 gone 判为缺失，实际 {view._missing}"
        assert view._picked["1"] is False
        assert view._tree.item("1", "values")[0] == UNPICKED
        assert view._tree.item("1", "values")[-1] == "文件缺失"
        assert view._tree.item("1", "tags") == ("missing",)

        view._toggle("1")
        assert view._picked["1"] is False, "缺失项不该被勾上"
        assert Path(real).is_file()
    finally:
        root.destroy()


@needs_display
def test_empty_dir_keeps_previous_tasks(tmp_path, monkeypatch):
    """空目录加载失败要保住上一批任务，并说明原因。

    静默清空的话，用户明明看到列表在，回来发现空了却不知道原因。
    """
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")
    empty = tmp_path / "empty"
    empty.mkdir()

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        assert len(view._tasks) == 1

        view._log.clear()
        _load_folder(root, view, empty, monkeypatch)

        assert len(view._tasks) == 1, "空目录不该清掉已有任务"
        assert "没有找到视频文件" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_yaml_mode_keeps_per_task_values(tmp_path, monkeypatch):
    """高级 yaml 入口不能被统一参数覆盖掉。

    yaml 的价值就在于逐任务能配不同的分区/标题，还有
    ``submit.backend``——一刀盖成统一值等于把这些能力废了。
    """
    import tkinter as tk

    real_a = _make_video(tmp_path, "a.mp4")
    real_b = _make_video(tmp_path, "b.mp4")
    path = _write_yaml(tmp_path, [("甲", str(real_a)), ("乙", str(real_b))])

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_yaml(root, view, path)

        assert view._source_mode == "yaml"
        assert view._cfg.submit.backend == "web", "yaml 的投稿后端要留着"
        assert [t.tag for t in view._tasks] == ["来自yaml的标签", "来自yaml的标签"]

        cfg, selected = view._snapshot_run([0, 1])
        assert cfg.submit.backend == "web"
        assert selected[0][1].tag == "来自yaml的标签"
        # 顶部参数在 yaml 模式下不生效
        assert selected[0][1].tid == 21
        assert "disabled" in view._tid_combo.state(), "yaml 模式顶部参数应置灰"
        assert "disabled" in view._tag_entry.state()
        assert "disabled" in view._dtime_entry.state()
    finally:
        root.destroy()


@needs_display
def test_back_to_folder_reenables_shared_params(tmp_path, monkeypatch):
    """从 yaml 切回文件夹，顶部参数要恢复可编辑。"""
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")
    path = _write_yaml(tmp_path, [("甲", str(tmp_path / "a.mp4"))])

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_yaml(root, view, path)
        assert "disabled" in view._tid_combo.state()

        _load_folder(root, view, tmp_path, monkeypatch)

        assert view._source_mode == "folder"
        assert "disabled" not in view._tid_combo.state()
        assert "disabled" not in view._tag_entry.state()
    finally:
        root.destroy()


@needs_display
def test_toggle_refuses_missing_file(tmp_path, monkeypatch):
    """点缺失项不该勾上——勾了却在执行时被跳过最让人困惑。"""
    import tkinter as tk

    real = _make_video(tmp_path, "a.mp4")
    doomed = _make_video(tmp_path, "b.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        # 只留下 a（index 0），b 被删掉后 index 1 变成缺失行
        doomed.unlink()
        view._reset_state()
        view._fill_tree(view._tasks)
        assert view._missing == {"1"}

        view._toggle("1")
        assert view._picked["1"] is False, "缺失项不该被勾上"
        assert view._tree.item("1", "values")[0] == UNPICKED
        assert Path(real).is_file()
    finally:
        root.destroy()


@needs_display
def test_select_failed_picks_only_errors(tmp_path, monkeypatch):
    """「只选失败项」要选中失败的，且排除文件缺失的。"""
    import tkinter as tk

    for name in ("a.mp4", "b.mp4"):
        _make_video(tmp_path, name)
    doomed = _make_video(tmp_path, "c.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        doomed.unlink()
        view._fill_tree(view._tasks)

        view._mark(0, "成功", "ok")
        view._mark(1, "失败：原因", "error")

        view._select_failed()
        assert view._selected_indexes() == [1], (
            f"应只选中 1 号，实际 {view._selected_indexes()}"
        )
    finally:
        root.destroy()


@needs_display
def test_select_all_never_picks_missing(tmp_path, monkeypatch):
    """全选也要跳过缺失项，否则执行时会扑空。"""
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")
    doomed = _make_video(tmp_path, "b.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        doomed.unlink()
        view._fill_tree(view._tasks)

        view._select_all()
        assert view._selected_indexes() == [0]
        assert view._picked["1"] is False
    finally:
        root.destroy()


@needs_display
def test_cancel_reverts_busy_rows(tmp_path, monkeypatch):
    """取消后「进行中」必须回到待投稿，否则看着像还在跑。"""
    import tkinter as tk

    from bilibili_submit.ui.workers import Cancelled

    for name in ("a.mp4", "b.mp4"):
        _make_video(tmp_path, name)

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        view._mark(0, "进行中", "busy")
        view._mark(1, "进行中", "busy")
        view._on_error(Cancelled())

        assert view._tree.item("0", "values")[-1] == "待投稿"
        assert view._tree.item("0", "tags") == ("idle",)
        assert view._tree.item("1", "values")[-1] == "待投稿"
        assert "已取消" in view._progress._label.cget("text")
    finally:
        root.destroy()


@needs_display
def test_event_updates_row_progress_and_stores_error(tmp_path, monkeypatch):
    """Event 到界面的映射：着色、截断、进度、完整错误都要对。"""
    import tkinter as tk

    for name in ("a.mp4", "b.mp4"):
        _make_video(tmp_path, name)

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        view._on_progress(Event("start", index=0, position=1, total=2))
        assert view._tree.item("0", "values")[-1] == "进行中"
        assert view._tree.item("0", "tags") == ("busy",)

        long = "601 投稿过于频繁，等待 30 分钟后重试，后面还有更长的解释"
        view._on_progress(
            Event(
                "done",
                index=0,
                position=1,
                total=2,
                status=f"失败：{long}",
                error=long,
            )
        )

        shown = view._tree.item("0", "values")[-1]
        assert shown.endswith("…") and len(shown) <= 18, f"状态列没截断：{shown!r}"
        assert view._tree.item("0", "tags") == ("error",)
        # 完整原因要留着，双击时弹窗用
        assert view._errors["0"] == long
        assert view._progress._bar.cget("value") == pytest.approx(50, abs=0.5)
    finally:
        root.destroy()


@needs_display
def test_plain_string_still_goes_to_log(tmp_path, monkeypatch):
    """字符串上报仍进日志——Event 不能把普通日志吞掉。"""
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        view._log.clear()

        view._on_progress("[1/1] 开始：a")
        assert "[1/1] 开始：a" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_summary_counts_and_retry_button(tmp_path, monkeypatch):
    """汇总条的成败计数与「重试失败项」的可用状态联动。"""
    import tkinter as tk

    for name in ("a.mp4", "b.mp4", "c.mp4"):
        _make_video(tmp_path, name)
    doomed = tmp_path / "c.mp4"

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        doomed.unlink()
        view._fill_tree(view._tasks)

        text = view._summary._stats.cget("text")
        assert "共 3" in text and "缺失 1" in text, text
        # 还没跑过，不该有成败计数
        assert "成功" not in text and "失败" not in text, text
        assert "disabled" in view._retry_button.state()

        view._mark(0, "成功", "ok")
        view._mark(1, "失败：x", "error")
        text = view._summary._stats.cget("text")
        assert "成功 1" in text and "失败 1" in text, text
        assert "disabled" not in view._retry_button.state(), "有失败项就该能重试"
    finally:
        root.destroy()


@needs_display
def test_run_without_selection_does_nothing(tmp_path, monkeypatch):
    """一个都没勾时点开始，应该提示而不是空跑一趟。"""
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        view._log.clear()

        view._select_none()
        view._run()
        assert "没有勾选" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_row_never_carries_two_tags(tmp_path, monkeypatch):
    """每行只打一个 tag——多个 tag 的背景色会互相打架。"""
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        for tone in ("busy", "ok", "error", "idle"):
            view._mark(0, "x", tone)
            tags = view._tree.item("0", "tags")
            assert len(tags) == 1, f"{tone} 之后 tag 变成了 {tags}"
            assert tags[0] == tone
    finally:
        root.destroy()


@needs_display
def test_do_run_reports_events_instead_of_touching_tk(tmp_path, monkeypatch):
    """``_do_run`` 只能经 report 上报，不能直接操作 Tk。

    这是本页此前的技术债：工作线程里直接调 ``self.after`` 更新列表。
    """
    import tkinter as tk

    from bilibili_submit.scheduler import TaskOutcome
    from bilibili_submit.ui.views import tasks as tasks_mod

    for name in ("a.mp4", "b.mp4"):
        _make_video(tmp_path, name)

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        # 把真实网络调用挡掉
        monkeypatch.setattr(
            tasks_mod, "run_task",
            lambda *a, **k: TaskOutcome(name="A", success=True, bvid="BV1", url="u"),
        )
        monkeypatch.setattr(tasks_mod, "get_backend", lambda *a, **k: object())
        monkeypatch.setattr(
            type(view.app.ctx), "client", lambda self, need_login=True: object()
        )

        # 记录有没有人碰过 Tk
        touched = []
        monkeypatch.setattr(
            view, "after", lambda *a, **k: touched.append(a)
        )

        _cfg, selected = view._snapshot_run([0, 1])
        seen: list[object] = []
        outcomes = view._do_run(seen.append, lambda: False, selected, _cfg)

        assert len(outcomes) == 2
        events = [m for m in seen if isinstance(m, Event)]
        assert len(events) == 4, f"应有 2 组 start/done，实际 {len(events)}"
        assert [e.kind for e in events] == ["start", "done", "start", "done"]
        assert not touched, f"_do_run 里不该直接调 after：{touched}"
    finally:
        root.destroy()


@needs_display
def test_do_run_uses_snapshot_not_live_tasks(tmp_path, monkeypatch):
    """执行途中换掉 ``self._tasks``，本轮投的仍该是启动时那份快照。

    没有快照的话，``_do_run`` 边跑边读 ``self._tasks``，用户中途点
    「重新扫描」就会把任务投给错的分区，或者直接 IndexError。
    """
    import tkinter as tk

    from bilibili_submit.config import TaskConfig
    from bilibili_submit.scheduler import TaskOutcome
    from bilibili_submit.ui.views import tasks as tasks_mod

    _make_video(tmp_path, "a.mp4")
    _make_video(tmp_path, "b.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        seen_tasks = []
        monkeypatch.setattr(
            tasks_mod, "run_task",
            lambda client, task, cfg, **k: (
                seen_tasks.append(task)
                or TaskOutcome(name="x", success=True, bvid="BV1", url="u")
            ),
        )
        monkeypatch.setattr(tasks_mod, "get_backend", lambda *a, **k: object())
        monkeypatch.setattr(
            type(view.app.ctx), "client", lambda self, need_login=True: object()
        )

        _cfg, selected = view._snapshot_run([0, 1])

        # 执行线程启动后，用户在主线程把任务列表整个换掉
        view._tasks = [TaskConfig(name="别的", file="/x/other.mp4")]
        view._cfg = tasks_mod.AppConfig()

        view._do_run(lambda _m: None, lambda: False, selected, _cfg)

        assert [t.name for t in seen_tasks] == ["a", "b"], (
            f"本轮该跑快照里的 a、b，实际 {[t.name for t in seen_tasks]}"
        )
    finally:
        root.destroy()


@needs_display
def test_do_run_reports_position_not_index(tmp_path, monkeypatch):
    """非连续勾选时，Event 的 position 必须是「第几个」而不是下标。

    下标 3 和 5 都被勾选时（total=2），若 position 填成 index，
    进度会算出 200% / 300%，状态栏还会显示「4/2」。
    """
    import tkinter as tk

    from bilibili_submit.scheduler import TaskOutcome
    from bilibili_submit.ui.views import tasks as tasks_mod

    for i in range(6):
        _make_video(tmp_path, f"v{i}.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        monkeypatch.setattr(
            tasks_mod, "run_task",
            lambda *a, **k: TaskOutcome(name="A", success=True, bvid="BV1", url="u"),
        )
        monkeypatch.setattr(tasks_mod, "get_backend", lambda *a, **k: object())
        monkeypatch.setattr(
            type(view.app.ctx), "client", lambda self, need_login=True: object()
        )

        cfg, selected = view._snapshot_run([3, 5])
        seen: list[object] = []
        view._do_run(seen.append, lambda: False, selected, cfg)
        events = [m for m in seen if isinstance(m, Event) and m.done]

        assert [e.position for e in events] == [1, 2]
        assert [e.total for e in events] == [2, 2]
        assert [round(e.percent) for e in events] == [50, 100]
        assert [round(e.index) for e in events] == [3, 5], "index 仍应是真实下标"
    finally:
        root.destroy()


@needs_display
def test_progress_bar_never_exceeds_total(tmp_path, monkeypatch):
    """端到端验证：非连续勾选下，进度条不会第一项就顶满。"""
    import tkinter as tk

    for i in range(6):
        _make_video(tmp_path, f"v{i}.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        view._on_progress(Event("done", index=3, position=1, total=2, status="成功"))
        assert view._progress._bar.cget("value") == pytest.approx(50, abs=0.5)
        assert view.app._status_task.cget("text") == "1/2"

        view._on_progress(Event("done", index=5, position=2, total=2, status="成功"))
        assert view._progress._bar.cget("value") == pytest.approx(100, abs=0.5)
        assert view.app._status_task.cget("text") == "2/2"
    finally:
        root.destroy()


@needs_display
def test_retry_button_stays_disabled_while_running(tmp_path, monkeypatch):
    """运行中出现失败项时，「重试失败项」不能被解开。

    ``_mark`` 每完成一项都会刷新汇总，如果汇总不看运行态，
    第一个失败项一出现按钮就变成可点，而 ``_run`` 会因「已有任务在
    运行」直接 return——成了按了没反应的假按钮。
    """
    import tkinter as tk

    for name in ("a.mp4", "b.mp4"):
        _make_video(tmp_path, name)

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        view._set_busy(True)
        assert "disabled" in view._retry_button.state()

        view._mark(0, "失败：x", "error")
        view._mark(1, "失败：y", "error")
        assert "disabled" in view._retry_button.state(), (
            "运行中不该解锁重试按钮"
        )

        view._set_busy(False)
        assert "disabled" not in view._retry_button.state(), "结束后应可重试"
    finally:
        root.destroy()


@needs_display
def test_selection_locked_while_running(tmp_path, monkeypatch):
    """执行期间不能改勾选，否则界面与实际执行的列表会静默脱节。"""
    import tkinter as tk

    for i in range(3):
        _make_video(tmp_path, f"v{i}.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        view._mark(1, "失败：x", "error")
        view._set_busy(True)

        before = view._selected_indexes()
        assert before == [0, 1, 2]

        # 每一步都断言：只在最后断言会被「先清空再全选」这种
        # 恰好抵消的操作掩盖掉，看不出中间的改动。
        view._select_failed()
        assert view._selected_indexes() == before, "运行中「只选失败项」不该生效"

        view._select_none()
        assert view._selected_indexes() == before, "运行中「全不选」不该生效"

        view._toggle("2")
        assert view._selected_indexes() == before, "运行中点勾选列不该生效"

        # 快捷按钮也应该被禁用，界面上不给按的机会
        for button in view._summary._buttons:
            assert "disabled" in button.state()
    finally:
        root.destroy()


@needs_display
def test_load_blocked_while_running(tmp_path, monkeypatch):
    """执行途中换目录会换掉工作线程正在读的任务列表，必须挡住。"""
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")
    other = tmp_path.parent / "other_videos"
    _make_video(other, "x.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        tasks_before = list(view._tasks)

        view._set_busy(True)
        view._dir_var.set(str(other))
        view._log.clear()
        view._load()

        assert view._tasks == tasks_before, "运行中不该换掉任务列表"
        assert "正在执行任务" in view._log._text.get("1.0", "end")
        assert "disabled" in view._load_button.state()
        assert "disabled" in view._pick_button.state()
        assert "disabled" in view._config_button.state(), "yaml 入口也要锁"
    finally:
        root.destroy()


@needs_display
def test_summary_unlocks_retry_while_thread_alive(tmp_path, monkeypatch):
    """收尾时线程还没退出，「重试失败项」也必须解锁。

    ``on_done`` / ``on_error`` 是排进 Tk 事件循环执行的，那时工作线程
    只是排完队就返回、**还没真正退出**。所以 ``_worker.running`` 在
    收尾瞬间仍是 True——拿它当「是否解锁」的判据，有失败项也点不了
    重试，而且之后没有任何东西会再来刷新一次。

    这里用真线程卡住来复现那个瞬间：线程 ``join`` 之前就调用收尾。
    """
    import tkinter as tk

    for name in ("a.mp4", "b.mp4"):
        _make_video(tmp_path, name)

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        view._mark(0, "失败：x", "error")
        view._set_busy(True)

        # 起一个真线程，只为让 running 在收尾时为 True
        release = threading.Event()
        started = threading.Event()

        def _hold():
            started.set()
            release.wait(3)

        holder = threading.Thread(target=_hold, daemon=True)
        holder.start()
        started.wait(2)
        monkeypatch.setattr(view._worker, "_thread", holder)
        assert view._worker.running is True, "本用例前提：线程仍活着"

        # 收尾：界面解锁，但线程还活着
        view._set_busy(False)
        view._update_summary()

        assert "disabled" not in view._retry_button.state(), (
            "线程还活着就不让重试，收尾后再没人刷新，有失败项也点不了"
        )
        release.set()
        holder.join(2)
    finally:
        root.destroy()


@needs_display
def test_run_failure_unlocks_buttons(tmp_path, monkeypatch):
    """线程启动失败也要把界面解锁。

    ``_busy`` 是执行锁的唯一真相来源，只在 ``_on_done`` / ``_on_error``
    里归位。启动阶段就抛异常的话这两个都不会来，按钮会永久灰着、
    取消也点不动，只能重启进程。
    """
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")

    root = tk.Tk()
    try:
        app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        view._log.clear()

        class _Ctx:
            logged_in = True

            def client(self, need_login=True):
                return object()

        app.ctx = _Ctx()

        def boom(*_a, **_k):
            raise RuntimeError("模拟启动失败")

        monkeypatch.setattr(view._worker, "run", boom)
        view._run()

        assert "disabled" not in view._run_button.state(), "启动失败后按钮应恢复"
        # 取消按钮反倒该是禁用的——压根没有任务在跑
        assert "disabled" in view._cancel_button.state()
        assert view._editable(), "_busy 必须归位，否则再也点不动"
        assert "无法启动任务" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_error_reverts_busy_rows(tmp_path, monkeypatch):
    """非取消的异常也要收尾，否则行永远停在「进行中」。

    cookie 过期时 ``_do_run`` 抛的是 ``NotLoggedInError``，不是
    ``Cancelled``——原先只有取消路径归位，异常路径直接把界面留在
    「进行中」，进度条也不复位，看着像还在跑。
    """
    import tkinter as tk

    from bilibili_submit.exceptions import NotLoggedInError

    for name in ("a.mp4", "b.mp4"):
        _make_video(tmp_path, name)

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        view._log.clear()

        view._mark(0, "进行中", "busy")
        view._on_error(NotLoggedInError("cookie 失效"))

        assert view._tree.item("0", "values")[-1] == "待投稿"
        assert view._tree.item("0", "tags") == ("idle",)
        assert "已中断" in view._progress._label.cget("text")
        assert "登录态已失效" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_retry_failed_keeps_selection_when_not_logged_in(tmp_path, monkeypatch):
    """未登录时点重试，不能把用户原有的勾选覆盖掉。

    ``_retry_failed`` 原来是「先只选失败项、再跑」——``_run`` 检查登录态
    后直接 return，用户那一堆勾选已经被改掉了。
    """
    import tkinter as tk

    for i in range(3):
        _make_video(tmp_path, f"v{i}.mp4")

    root = tk.Tk()
    try:
        app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)
        view._mark(0, "失败：x", "error")
        view._mark(1, "成功", "ok")
        view._mark(2, "失败：y", "error")
        before = view._selected_indexes()
        assert before == [0, 1, 2]

        # 换成一个未登录的 ctx，模拟 cookie 失效
        real_ctx = app.ctx

        class _LoggedOut:
            logged_in = False

        view.app.ctx = _LoggedOut()
        try:
            view._log.clear()
            view._retry_failed()
            assert view._selected_indexes() == before, "勾选不该被改掉"
            assert "尚未登录" in view._log._text.get("1.0", "end")
        finally:
            view.app.ctx = real_ctx
    finally:
        root.destroy()


# ---------- 偏好恢复 ----------


@needs_display
def test_state_saved_and_restored_across_windows(tmp_path, monkeypatch):
    """目录和顶部参数要能跨启动恢复。

    两个真窗口先后建：第一个填好并保存，销毁，第二个从同一个状态文件
    恢复并自动重扫。用真 ``mainloop`` 等，不用 ``update_idletasks``。
    """
    import tkinter as tk

    for name in ("a.mp4", "b.mp4"):
        _make_video(tmp_path, name)

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._dir_var.set(str(tmp_path))
        view._tid_var.set("17 - 单机游戏")
        view._tag_var.set("日常,记录")
        view._desc_var.set("统一简介")
        view._dtime_var.set("6")
        view._save_state()
        run_until(root, lambda: view._state_file.is_file())
    finally:
        root.destroy()

    assert (tmp_path / "ui-state.json").is_file()

    root2 = tk.Tk()
    try:
        _app, view2 = _build(root2, tmp_path, monkeypatch)
        view2.refresh()  # 触发恢复
        run_until(root2, lambda: not view2._busy and view2._tasks)

        assert view2._dir_var.get() == str(tmp_path)
        assert view2._tag_var.get() == "日常,记录"
        assert view2._desc_var.get() == "统一简介"
        assert view2._dtime_var.get() == "6"
        assert len(view2._tasks) == 2, "目录还在就该自动重扫"
        assert len(view2._tree.get_children()) == 2
    finally:
        root2.destroy()


@needs_display
def test_restore_reports_missing_directory(tmp_path, monkeypatch):
    """上次的目录没了要说明，不能静默清掉。

    静默清掉的话用户会以为程序把设置弄丢了。
    """
    import tkinter as tk

    from bilibili_submit.ui.state import BatchUIState, save_ui_state

    gone = tmp_path / "已移动的目录"
    state_file = tmp_path / "ui-state.json"
    save_ui_state(BatchUIState(directory=str(gone)), state_file)
    assert not gone.exists()

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view.refresh()

        run_until(root, lambda: "已不存在" in view._log._text.get("1.0", "end"))
        assert view._dir_var.get() == str(gone), "路径要留着让用户看见并改"
        assert view._tasks == [], "目录没了不该凭空造任务"
    finally:
        root.destroy()


@needs_display
def test_refresh_only_restores_once(tmp_path, monkeypatch):
    """恢复只做一次，否则每次切页都重扫、执行到一半切页选择被清空。"""
    import tkinter as tk

    _make_video(tmp_path, "a.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._dir_var.set(str(tmp_path))
        view._load()
        run_until(root, lambda: not view._busy and view._tasks)
        view._select_none()

        view.refresh()
        root.update_idletasks()

        assert view._selected_indexes() == [], "已有选择不该被第二次恢复冲掉"
        assert view._restored is True
    finally:
        root.destroy()


@needs_display
def test_corrupt_state_file_is_reported(tmp_path, monkeypatch):
    """偏好文件损坏要提示，别装作首次使用。"""
    import tkinter as tk

    (tmp_path / "ui-state.json").write_text("{ 坏了", encoding="utf-8")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view.refresh()
        assert "读取失败" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_invalid_dtime_keeps_last_good_state(tmp_path, monkeypatch):
    """延时填到一半时保存，不该把上次那个能用的值覆盖掉。"""
    import tkinter as tk

    from bilibili_submit.ui.state import BatchUIState

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._tid_var.set("17 - 单机游戏")
        view._dtime_var.set("6")
        view._save_state()
        run_until(root, lambda: view._state_file.is_file())

        view._dtime_var.set("六")
        current = view._current_state()
        assert current.tid == 17, "能解析的字段照常保存"
        assert current.dtime_offset_hours is None, "非数字不该被写进去"

        saved, _ = __import__(
            "bilibili_submit.ui.state", fromlist=["load_ui_state"]
        ).load_ui_state(view._state_file)
        assert saved.dtime_offset_hours == 6.0, "上次那个值该留着"
        assert saved.tid == 17
    finally:
        root.destroy()
    assert BatchUIState().dtime_offset_hours is None


# ---------- 分 P 合并 ----------


@needs_display
def test_group_switch_merges_series_into_one_task(tmp_path, monkeypatch):
    """开着分 P 合并扫目录：同一套视频应变成一个 multip 任务。"""
    import tkinter as tk

    _make_video(tmp_path, "旅行_01.mp4")
    _make_video(tmp_path, "旅行_02.mp4")
    _make_video(tmp_path, "旅行_03.mp4")
    _make_video(tmp_path, "教程.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._group_var.set("按文件名前缀分组")
        _load_folder(root, view, tmp_path, monkeypatch)

        tasks = view._tasks
        names = {t.name: (t.type, len(t.files or ([t.file] if t.file else [])))
                 for t in tasks}
        assert names["旅行"][0] == "multip"
        assert names["旅行"][1] == 3, "三个分P合进一个稿件"
        assert names["教程"][0] == "single", "孤立文件仍各自投稿"
    finally:
        root.destroy()


@needs_display
def test_group_switch_off_keeps_one_task_per_file(tmp_path, monkeypatch):
    """默认不合并：每个视频一个稿件（与加这个功能之前完全一致）。"""
    import tkinter as tk

    _make_video(tmp_path, "旅行_01.mp4")
    _make_video(tmp_path, "旅行_02.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        assert view._group_var.get() == "不合并"
        _load_folder(root, view, tmp_path, monkeypatch)

        assert len(view._tasks) == 2
        assert all(t.type == "single" for t in view._tasks)
    finally:
        root.destroy()


@needs_display
def test_grouped_task_shows_part_count_in_tree(tmp_path, monkeypatch):
    """列表的「分P」列要能一眼看出哪个是多 P 稿件。"""
    import tkinter as tk

    _make_video(tmp_path, "旅行_01.mp4")
    _make_video(tmp_path, "旅行_02.mp4")
    _make_video(tmp_path, "教程.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._group_var.set("按文件名前缀分组")
        _load_folder(root, view, tmp_path, monkeypatch)

        rows = {
            view._tree.item(iid, "values")[1]: view._tree.item(iid, "values")[3]
            for iid in view._tree.get_children()
        }
        assert rows["旅行"] == "2"
        assert rows["教程"] == "—"
    finally:
        root.destroy()


@needs_display
def test_missing_file_marks_whole_multipart_task(tmp_path, monkeypatch):
    """多 P 稿件缺一个文件就不能投——整行标缺失，不能只标那一个。

    先建好再删，模拟"扫描过后文件被移走"的真实情况。
    """
    import tkinter as tk

    _make_video(tmp_path, "旅行_01.mp4")
    gone = _make_video(tmp_path, "旅行_02.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._group_var.set("按文件名前缀分组")
        _load_folder(root, view, tmp_path, monkeypatch)
        assert not view._missing, "扫描时两个文件都还在"

        gone.unlink()
        view._fill_tree(view._tasks)

        children = view._tree.get_children()
        assert len(view._missing) == 1, "缺一个分P，整行就该算缺失"
        assert "缺 1/2" in view._tree.item(children[0], "values")[2]
    finally:
        root.destroy()


@needs_display
def test_edit_parts_writes_titles_back(tmp_path, monkeypatch):
    """改完分 P 标题要落回任务对象，执行时才会带上。"""
    import tkinter as tk

    _make_video(tmp_path, "旅行_01.mp4")
    _make_video(tmp_path, "旅行_02.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._group_var.set("按文件名前缀分组")
        _load_folder(root, view, tmp_path, monkeypatch)

        seen: dict[str, object] = {}

        def fake_ask(archive, files, current):
            seen["archive"] = archive
            seen["current"] = current
            return ["出发", "到达"]

        monkeypatch.setattr(view, "_ask_part_titles", fake_ask)
        view._edit_parts("0")

        assert seen["current"] == ["旅行_01", "旅行_02"], "默认标题取文件名"
        task = view._tasks[0]
        assert task.part_titles == ["出发", "到达"]
        assert task.files and len(task.files) == 2, "文件列表不该被动过"
    finally:
        root.destroy()


@needs_display
def test_edit_parts_ignores_single_file_task(tmp_path, monkeypatch):
    """单 P 任务双击不该弹窗——没什么可改的。"""
    import tkinter as tk

    _make_video(tmp_path, "教程.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        _load_folder(root, view, tmp_path, monkeypatch)

        def boom(*_a, **_k):
            raise AssertionError("单P任务不该弹分P标题对话框")

        monkeypatch.setattr(view, "_ask_part_titles", boom)
        view._edit_parts("0")  # 不抛即通过
    finally:
        root.destroy()


# ---------- 按文件夹分 P ----------


@needs_display
def test_group_combo_offers_three_choices(tmp_path, monkeypatch):
    """「分P合并」下拉框要给出三种：不合并 / 按文件名前缀 / 按文件夹。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        assert tuple(view._group_combo.cget("values")) == GROUP_OPTIONS
        assert GROUP_FOLDER in GROUP_OPTIONS
    finally:
        root.destroy()


@needs_display
def test_folder_mode_merges_each_subfolder(tmp_path, monkeypatch):
    """选「按文件夹分组」：每个子文件夹合成一个稿件。"""
    import tkinter as tk

    from bilibili_submit.config import task_files

    video_root = tmp_path / "视频"
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        _make_video(video_root / "旅行", name)
    _make_video(video_root / "教程", "x.mp4")
    _make_video(video_root, "solo.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._group_var.set(GROUP_FOLDER)
        _load_folder(root, view, video_root, monkeypatch)

        by_title = {t.title: (t.type, len(task_files(t))) for t in view._tasks}
        assert by_title["旅行"] == ("multip", 3), "子文件夹三个视频合成一个三 P 稿件"
        assert by_title["教程"] == ("single", 1), "单文件子文件夹仍是单 P，标题取文件夹名"
        assert by_title["solo"] == ("single", 1), "根目录散落文件不合并"
    finally:
        root.destroy()


@needs_display
def test_folder_mode_ignores_third_level(tmp_path, monkeypatch):
    """只钻一层：第三层的视频不进列表，免得指到大目录冒出上千任务。"""
    import tkinter as tk

    video_root = tmp_path / "视频"
    _make_video(video_root / "旅行", "a.mp4")
    _make_video(video_root / "旅行" / "更深", "z.mp4")

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._group_var.set(GROUP_FOLDER)
        _load_folder(root, view, video_root, monkeypatch)

        titles = {t.title for t in view._tasks}
        assert titles == {"旅行"}, f"不应出现第三层的内容，实际 {titles}"
    finally:
        root.destroy()


@needs_display
def test_group_mode_is_saved_as_preference(tmp_path, monkeypatch):
    """切换分组方式要记进偏好，下次打开还是它。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._group_var.set(GROUP_FOLDER)
        assert view._current_state().group_mode == "folder"

        view._group_var.set(GROUP_PREFIX)
        assert view._current_state().group_mode == "prefix"

        view._group_var.set(GROUP_OFF)
        assert view._current_state().group_mode == "none"
    finally:
        root.destroy()


@needs_display
def test_unknown_group_label_falls_back_to_none(tmp_path, monkeypatch):
    """下拉框被改成没见过的值时退回"不合并"，而不是让扫描失败。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._group_var.set("乱填的")
        assert view._group_mode() == "none"
    finally:
        root.destroy()


@needs_display
def test_folder_mode_end_to_end_submits_once_per_folder(tmp_path, monkeypatch):
    """从扫描到投递跑完整链路：上传次数 = 视频数，投递次数 = 稿件数。

    UI 测试只验证了"扫出哪些任务"，多 P 真正的语义是**上传 N 次、投递
    一次**——这两者分开测过，合起来跑一遍才敢说链路是通的。
    """
    import tkinter as tk

    from bilibili_submit import scheduler
    from bilibili_submit.config import AppConfig, task_files
    from bilibili_submit.submit import SubmitResult

    video_root = tmp_path / "视频"
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        _make_video(video_root / "旅行", name)
    _make_video(video_root / "教程", "x.mp4")
    _make_video(video_root, "solo.mp4")

    uploaded: list[str] = []
    submitted: list[list[str]] = []

    def fake_upload(_client, video, **_kw):
        uploaded.append(Path(video).name)

        class _Result:
            filename = f"upos-{Path(video).stem}"

        return _Result()

    def fake_submit(_client, meta, **_kw):
        submitted.append([v["filename"] for v in meta.videos])
        return SubmitResult(aid=100, bvid="BV1xx")

    monkeypatch.setattr(scheduler, "upload_video", fake_upload)
    monkeypatch.setattr(scheduler, "submit_archive", fake_submit)

    class _FakeClient:
        """只为满足 run_task 的签名——真正的网络调用都在上面被打桩了。"""

    cfg = AppConfig()
    cfg.defaults.tid = 21

    root = tk.Tk()
    try:
        _app, view = _build(root, tmp_path, monkeypatch)
        view._group_var.set(GROUP_FOLDER)
        _load_folder(root, view, video_root, monkeypatch)

        for task in view._tasks:
            scheduler.run_task(_FakeClient(), task, cfg)

        assert len(uploaded) == 5, f"5 个视频应上传 5 次，实际 {uploaded}"
        assert len(submitted) == 3, f"3 个稿件应投递 3 次，实际 {submitted}"
        assert sorted(len(parts) for parts in submitted) == [1, 1, 3], (
            f"分 P 数应为 1/1/3，实际 {submitted}"
        )
    finally:
        root.destroy()
