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
    tmp_path.mkdir(parents=True, exist_ok=True)
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


@needs_display
def test_do_run_reports_position_not_index(tmp_path, monkeypatch):
    """非连续勾选时，Event 的 position 必须是「第几个」而不是下标。

    下标 3 和 5 都被勾选时（total=2），若 position 填成 index，
    进度会算出 200% / 300%，状态栏还会显示「4/2」。
    """
    import tkinter as tk

    from bilibili_submit.scheduler import TaskOutcome
    from bilibili_submit.ui.views import tasks as tasks_mod

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(
        tmp_path,
        [("A", real), ("B", real), ("C", real), ("D", real), ("E", real), ("F", real)],
    )

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

        monkeypatch.setattr(
            tasks_mod, "run_task",
            lambda *a, **k: TaskOutcome(name="A", success=True, bvid="BV1", url="u"),
        )
        monkeypatch.setattr(tasks_mod, "get_backend", lambda *a, **k: object())
        monkeypatch.setattr(
            type(view.app.ctx), "client", lambda self, need_login=True: object()
        )

        seen: list[object] = []
        view._do_run(seen.append, lambda: False, [3, 5])
        events = [m for m in seen if isinstance(m, Event) and m.done]

        assert [e.position for e in events] == [1, 2]
        assert [e.total for e in events] == [2, 2]
        assert [round(e.percent) for e in events] == [50, 100]
        assert [round(e.index) for e in events] == [3, 5], "index 仍应是真实下标"
    finally:
        root.destroy()


@needs_display
def test_progress_bar_never_exceeds_total(tmp_path):
    """端到端验证：非连续勾选下，进度条不会第一项就顶满。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(
        tmp_path,
        [("A", real), ("B", real), ("C", real), ("D", real), ("E", real), ("F", real)],
    )

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

        view._on_progress(Event("done", index=3, position=1, total=2, status="成功"))
        assert view._progress._bar.cget("value") == pytest.approx(50, abs=0.5)
        assert view.app._status_task.cget("text") == "1/2"

        view._on_progress(Event("done", index=5, position=2, total=2, status="成功"))
        assert view._progress._bar.cget("value") == pytest.approx(100, abs=0.5)
        assert view.app._status_task.cget("text") == "2/2"
    finally:
        root.destroy()


@needs_display
def test_retry_button_stays_disabled_while_running(tmp_path):
    """运行中出现失败项时，「重试失败项」不能被解开。

    ``_mark`` 每完成一项都会刷新汇总，如果汇总不看运行态，
    第一个失败项一出现按钮就变成可点，而 ``_run`` 会因「已有任务在
    运行」直接 return——成了按了没反应的假按钮。
    """
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()

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
def test_selection_locked_while_running(tmp_path):
    """执行期间不能改勾选，否则界面与实际执行的列表会静默脱节。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real), ("C", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()
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
def test_load_blocked_while_running(tmp_path):
    """执行途中换配置会换掉工作线程正在读的任务列表，必须挡住。"""
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real)])
    other = _write_config(tmp_path.parent / "other", [("X", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()
        tasks_before = list(view._tasks)

        view._set_busy(True)
        view._path_var.set(other)
        view._log.clear()
        view._load()

        assert view._tasks == tasks_before, "运行中不该换掉任务列表"
        assert "正在执行任务" in view._log._text.get("1.0", "end")
        assert "disabled" in view._load_button.state()
        assert "disabled" in view._pick_button.state()
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
    import threading

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real)])

    root = tk.Tk()
    try:
        _app, view = _build(root)
        view._path_var.set(cfg)
        view._load()
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

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real)])

    root = tk.Tk()
    try:
        app, view = _build(root)
        view._path_var.set(cfg)
        view._load()
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
def test_error_reverts_busy_rows(tmp_path):
    """非取消的异常也要收尾，否则行永远停在「进行中」。

    cookie 过期时 ``_do_run`` 抛的是 ``NotLoggedInError``，不是
    ``Cancelled``——原先只有取消路径归位，异常路径直接把界面留在
    「进行中」，进度条也不复位，看着像还在跑。
    """
    import tkinter as tk

    from bilibili_submit.exceptions import NotLoggedInError

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real)])

    root = tk.Tk()
    try:
        app, view = _build(root)
        view._path_var.set(cfg)
        view._load()
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
def test_retry_failed_keeps_selection_when_not_logged_in(tmp_path):
    """未登录时点重试，不能把用户原有的勾选覆盖掉。

    ``_retry_failed`` 原来是「先只选失败项、再跑」——``_run`` 检查登录态
    后直接 return，用户那一堆勾选已经被改掉了。
    """
    import tkinter as tk

    real = _make_video(tmp_path, "real.mp4")
    cfg = _write_config(tmp_path, [("A", real), ("B", real), ("C", real)])

    root = tk.Tk()
    try:
        app, view = _build(root)
        view._path_var.set(cfg)
        view._load()
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
