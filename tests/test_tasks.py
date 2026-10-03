"""批量任务页测试。

这一页是 UI 里业务逻辑最重的一块：勾选、文件预检、状态着色、
取消归位，彼此还有联动（比如「只选失败项」要排除缺失项）。
所以拆成两类：

1. **纯逻辑**（默认跑）：截断、语义色映射、Event 结构——不需要显示器
2. **需要 Tk**（无 display 时跳过）：真的建窗口、加载配置、点列表

第 2 类用一个临时生成的配置文件，其中**故意包含一个不存在的文件**，
用来验证预检——这是本页最容易出错也最有用的功能。
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.ui.views.tasks import PICKED, UNPICKED, TasksView, _truncate  # noqa: E402
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


def _build(root):
    """建 App 并返回批量任务页视图。"""
    import tkinter as tk  # noqa: F401
    from tkinter import ttk

    from bilibili_submit.ui import theme
    from bilibili_submit.ui.app import App

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    theme.apply(style)
    app = App(root)
    app.pack(fill="both", expand=True)
    return app, app._views["批量任务"]


def _write_config(tmp_path: Path, entries: list[tuple[str, str]]) -> str:
    """写一份配置；``entries`` 是 (任务名, 文件路径) 列表。"""
    lines = [
        "defaults:",
        "  tid: 21",
        "  tag: \"测试\"",
        "account:",
        f"  cookie_file: {tmp_path / 'cookie.json'}",
        "submit:",
        "  backend: web",
        "tasks:",
    ]
    for name, file_path in entries:
        lines.append(f"  - name: \"{name}\"")
        lines.append(f"    file: {file_path}")
        lines.append(f"    title: \"{name}\"")
    path = tmp_path / "config.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _make_video(tmp_path: Path, name: str) -> str:
    video = tmp_path / name
    video.write_bytes(b"\x00" * 1024)
    return str(video)


@needs_display
def test_load_marks_missing_files_and_skips_them(tmp_path):
    """文件不存在的任务要被标成缺失、默认不勾、且勾不上。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    gone = str(tmp_path / "gone.mp4")
    cfg = _write_config(tmp_path, [("存在", real), ("不见了", gone)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()
        root.update_idletasks()

        assert view._missing == {"1"}, f"应只把 1 号判为缺失，实际 {view._missing}"

        # 缺失项：未勾选 + 状态是「文件缺失」+ 用 missing 语义色
        assert view._picked["1"] is False
        values = view._tree.item("1", "values")
        assert values[0] == UNPICKED
        assert values[-1] == "文件缺失"
        assert view._tree.item("1", "tags") == ("missing",)

        # 存在的项：默认勾选 + 待投稿
        assert view._picked["0"] is True
        assert view._tree.item("0", "values")[0] == PICKED
        assert view._tree.item("0", "tags") == ("idle",)
    finally:
        root.destroy()


@needs_display
def test_toggle_refuses_missing_file(tmp_path):
    """点缺失项不该勾上——勾了却在执行时被跳过最让人困惑。"""
    import tkinter as tk

    gone = str(tmp_path / "gone.mp4")
    cfg = _write_config(tmp_path, [("不见了", gone)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

        view._toggle("0")
        assert view._picked["0"] is False, "缺失项不该被勾上"
        assert view._tree.item("0", "values")[0] == UNPICKED
    finally:
        root.destroy()


@needs_display
def test_select_failed_picks_only_errors(tmp_path):
    """「只选失败项」要选中失败的，且排除文件缺失的。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    gone = str(tmp_path / "gone.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real), ("C", gone)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

        view._mark(0, "成功", "ok")
        view._mark(1, "失败：原因", "error")

        view._select_failed()
        assert view._selected_indexes() == [1], (
            f"应只选中 1 号，实际 {view._selected_indexes()}"
        )
    finally:
        root.destroy()


@needs_display
def test_select_all_never_picks_missing(tmp_path):
    """全选也要跳过缺失项，否则执行时会扑空。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    gone = str(tmp_path / "gone.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", gone)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

        view._select_all()
        assert view._selected_indexes() == [0]
        assert view._picked["1"] is False
    finally:
        root.destroy()


@needs_display
def test_cancel_reverts_busy_rows(tmp_path):
    """取消后「进行中」必须回到待投稿，否则看着像还在跑。"""
    import tkinter as tk

    from bilibili_submit.ui.workers import Cancelled

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

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
def test_event_updates_row_progress_and_stores_error(tmp_path):
    """Event 到界面的映射：着色、截断、进度、完整错误都要对。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

        view._on_progress(Event("start", index=0, total=2))
        assert view._tree.item("0", "values")[-1] == "进行中"
        assert view._tree.item("0", "tags") == ("busy",)

        long = "601 投稿过于频繁，等待 30 分钟后重试，后面还有更长的解释"
        view._on_progress(Event("done", index=0, total=2, status=f"失败：{long}", error=long))

        shown = view._tree.item("0", "values")[-1]
        assert shown.endswith("…") and len(shown) <= 18, f"状态列没截断：{shown!r}"
        assert view._tree.item("0", "tags") == ("error",)
        # 完整原因要留着，双击时弹窗用
        assert view._errors["0"] == long
        assert view._progress._bar.cget("value") == pytest.approx(50, abs=0.5)
    finally:
        root.destroy()


@needs_display
def test_plain_string_still_goes_to_log(tmp_path):
    """字符串上报仍进日志——Event 不能把普通日志吞掉。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()
        view._log.clear()

        view._on_progress("[1/1] 开始：A")
        assert "[1/1] 开始：A" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_summary_counts_and_retry_button(tmp_path):
    """汇总条的成败计数与「重试失败项」的可用状态联动。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    gone = str(tmp_path / "gone.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real), ("C", gone)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

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
def test_run_without_selection_does_nothing(tmp_path):
    """一个都没勾时点开始，应该提示而不是空跑一趟。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()
        view._log.clear()

        view._select_none()
        view._run()
        assert "没有勾选" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_row_never_carries_two_tags(tmp_path):
    """每行只打一个 tag——多个 tag 的背景色会互相打架。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

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

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

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

        seen: list[object] = []
        outcomes = view._do_run(seen.append, lambda: False, [0, 1])

        assert len(outcomes) == 2
        events = [m for m in seen if isinstance(m, Event)]
        assert len(events) == 4, f"应有 2 组 start/done，实际 {len(events)}"
        assert [e.kind for e in events] == ["start", "done", "start", "done"]
        assert not touched, f"_do_run 里不该直接调 after：{touched}"
    finally:
        root.destroy()
