"""界面层测试。

分成两类：

1. **纯逻辑**（默认跑）：二维码矩阵、分区解析、主题常量——不需要显示器
2. **需要 Tk**（无 display 时自动跳过）：真的建窗口、切页面，抓布局错误

GUI 最容易翻车的地方是「import 没问题，一运行就炸」——
grid 配置错、回调签名不对、样式名写错，都只在 create_widget 时才暴露。
所以第 2 类测试宁可跳过也要保留，CI 上有 display 时会真跑一遍。
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.ui import theme  # noqa: E402


# ---------- 纯逻辑 ----------


def test_theme_colors_are_hex():
    """颜色统一用 #RRGGBB，避免混进 'red' 这类命名色导致风格漂移。"""
    for name in (
        "PRIMARY", "PRIMARY_DARK", "PRIMARY_SOFT", "BG", "SURFACE",
        "TEXT", "TEXT_SECONDARY", "SUCCESS", "WARNING", "DANGER",
    ):
        value = getattr(theme, name)
        assert value.startswith("#") and len(value) == 7, f"{name}={value} 不是合法色值"


def test_theme_font_family_is_platform_specific():
    """字体按平台选：写死 Windows 字体在 Linux 上会渲染成方框。"""
    assert theme.FAMILY
    if sys.platform == "win32":
        assert "Segoe" in theme.FAMILY, "Fluent Design 的标准字体是 Segoe UI"
    elif sys.platform == "darwin":
        assert "PingFang" in theme.FAMILY


def test_theme_fluent_metrics():
    """Fluent Design 的关键度量：栅格、圆角、控件高度。

    这些数字是设计规范的承诺，不是随便填的——改了要连同所有视图
    一起重排，所以在这里钉死。
    """
    assert theme.GRID == 8
    for name in ("PAD_XS", "PAD_SM", "PAD_MD", "PAD_LG", "PAD_XL", "PAD_2XL"):
        assert getattr(theme, name) % (theme.GRID // 2) == 0, name
    assert 4 <= theme.RADIUS_CONTROL <= 8, "Fluent 要求控件圆角 4~8px"
    assert theme.CONTROL_HEIGHT == 32, "按钮/输入框标准高度 32px"
    assert theme.PRIMARY_BUTTON_HEIGHT == 40, "主要操作按钮 40px"
    assert theme.PRIMARY_BUTTON_HEIGHT > theme.CONTROL_HEIGHT


def test_theme_has_both_light_and_dark_palettes():
    """浅色/深色两套色板键一致，切换模式后常用色都要跟着变。"""
    light, dark = theme.palette_for("light"), theme.palette_for("dark")
    assert light.keys() == dark.keys()
    for key in ("BG", "SURFACE", "PRIMARY", "TEXT", "COLLAPSE_BG", "FIELD_BG"):
        assert key in light, f"色板缺 {key}"
    assert light["BG"] != dark["BG"]

    theme.set_mode("dark")
    try:
        assert theme.MODE == "dark"
        assert theme.BG == dark["BG"]
        assert theme.SURFACE == dark["SURFACE"]
    finally:
        theme.set_mode("light")  # 其它测试假定浅色
    assert theme.BG == light["BG"]


def test_theme_sizes_are_positive():
    for name in ("NAV_WIDTH", "QR_SIZE", "MIN_WIDTH", "MIN_HEIGHT"):
        assert getattr(theme, name) > 0


def test_min_window_fits_content():
    """最小尺寸要装得下导航栏和二维码，否则小屏上布局会挤烂。"""
    assert theme.MIN_WIDTH > theme.NAV_WIDTH + theme.QR_SIZE
    assert theme.MIN_HEIGHT > theme.QR_SIZE * 2


def test_qr_matrix_is_square_and_nonempty():
    pytest.importorskip("qrcode")
    from bilibili_submit.ui.qr import qr_matrix

    matrix = qr_matrix("https://example.com/login")
    assert matrix, "矩阵不应为空"
    assert len(matrix) == len(matrix[0]), "必须是正方形"
    assert all(len(row) == len(matrix) for row in matrix)
    assert any(any(row) for row in matrix), "至少要有一个黑块"


def test_tid_parsing():
    """下拉框选项是「21 - 日常」这种，要能取回分区号。

    投稿页和批量任务页共用这一个函数，两边的分区列表才不会哪天不一样。
    """
    from bilibili_submit.ui.views.upload import TID_OPTIONS, parse_tid

    assert parse_tid("21 - 日常") == 21
    assert parse_tid("171 - 电子竞技") == 171
    # 无法解析时回退默认值，而不是抛异常打断表单填写
    assert parse_tid("") == 21
    assert parse_tid("乱七八糟") == 21

    # 每个选项都要能往返解析，否则用户选了下拉框却投到别的分区
    for text in TID_OPTIONS:
        head = text.split(" - ")[0]
        assert parse_tid(text) == int(head), text


def test_history_time_formatting():
    from bilibili_submit.ui.views.history import _format_time

    assert _format_time(0) == "?"
    assert _format_time(None) == "?"
    assert _format_time("abc") == "?"
    formatted = _format_time(1700000000)
    assert formatted != "?" and len(formatted) == 16


def test_gui_available_returns_reason():
    """返回 (bool, str) 而不是 bool：CLI 要把原因显示给用户。"""
    from bilibili_submit.ui import gui_available

    ok, reason = gui_available()
    assert isinstance(ok, bool)
    assert isinstance(reason, str)
    if not ok:
        assert reason, "不可用时必须给出原因"


def test_read_history_distinguishes_missing_from_broken(tmp_path):
    """「没有历史」和「历史文件坏了」必须能区分开。

    早期 read_history 把两者都返回空列表，界面上就只显示
    「暂无投稿历史」——文件明明坏了，用户却以为程序把记录弄丢了。
    """
    import json

    from bilibili_submit.scheduler import read_history_diagnose

    missing = tmp_path / "nope.json"
    entries, problem = read_history_diagnose(str(missing))
    assert entries == [] and problem == "", "文件不存在是正常状态，不该报错"

    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    entries, problem = read_history_diagnose(str(empty))
    assert entries == [] and problem == "", "空文件等同于没有历史"

    broken = tmp_path / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    entries, problem = read_history_diagnose(str(broken))
    assert entries == []
    assert "损坏" in problem, f"应说明文件损坏，实际: {problem!r}"
    assert str(broken) in problem, "报错应带上文件路径，方便用户处理"

    wrong_shape = tmp_path / "dict.json"
    wrong_shape.write_text(json.dumps({"a": 1}), encoding="utf-8")
    entries, problem = read_history_diagnose(str(wrong_shape))
    assert entries == []
    assert "格式异常" in problem, f"应说明结构不对，实际: {problem!r}"

    # 列表里混进非字典项时过滤掉，别让渲染时炸
    mixed = tmp_path / "mixed.json"
    mixed.write_text(json.dumps([{"bvid": "BV1"}, 123, "x"]), encoding="utf-8")
    entries, problem = read_history_diagnose(str(mixed))
    assert problem == ""
    assert len(entries) == 1 and entries[0]["bvid"] == "BV1"


def test_read_history_stays_lenient_for_writers():
    """read_history 本身仍不能抛异常。

    append_history 复用它来「读旧记录再整体写回」，
    一旦抛异常，损坏的历史文件会让之后所有投稿记录都写不进去。
    """
    from bilibili_submit.scheduler import append_history, read_history

    broken = Path(os.environ.get("TMPDIR", "/tmp")) / "test-history-lenient.json"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{ broken", encoding="utf-8")
    try:
        assert read_history(str(broken)) == [], "损坏文件应返回空列表而非抛异常"
    finally:
        broken.unlink(missing_ok=True)
    assert callable(append_history)


def test_worker_cancel_is_cooperative():
    """取消只是置标志位，任务自己检查——不能强杀线程。"""
    from bilibili_submit.ui.workers import Cancelled, Worker

    assert issubclass(Cancelled, Exception)
    assert hasattr(Worker, "cancel")
    assert hasattr(Worker, "run")


def test_safe_after_swallows_tcl_error_on_register():
    """窗口销毁后再注册 ``after`` 会抛 TclError，必须静默跳过。

    只包回调不够：异常从**注册**那一行就漏出去了，而它发生在工作
    线程里，一抛整条线程就死——收不到 on_done 也收不到 on_error，
    界面会永远停在「执行中」。
    """
    import tkinter as tk

    from bilibili_submit.ui.workers import safe_after

    class _Dead:
        """假装窗口已销毁。"""

        def after(self, *_a, **_k):
            raise tk.TclError("invalid command name 'after'")

    assert safe_after(_Dead(), 0, lambda: None) == ""


def test_safe_after_swallows_runtime_error_on_register():
    """主线程不在事件循环里时 ``after`` 抛 RuntimeError，同样要吞掉。"""
    from bilibili_submit.ui.workers import safe_after

    class _NoLoop:
        def after(self, *_a, **_k):
            raise RuntimeError("main thread is not in main loop")

    assert safe_after(_NoLoop(), 0, lambda: None) == ""


def test_safe_after_swallows_tcl_error_in_callback():
    """回调执行时窗口已被销毁，也要静默跳过。"""
    import tkinter as tk

    from bilibili_submit.ui.workers import safe_after

    fired = []

    class _Root:
        def after(self, _delay, fn):
            fired.append(fn)
            return "id"

    def boom():
        raise tk.TclError("invalid command name")

    assert safe_after(_Root(), 0, boom) == "id"
    fired[0]()          # 不应抛异常


def test_safe_after_still_runs_callback():
    """正常路径不能被加固带偏——回调必须真的执行，参数要原样传。"""
    from bilibili_submit.ui.workers import safe_after

    seen = []
    fired = []

    class _Root:
        def after(self, delay, fn):
            fired.append((delay, fn))
            return "id"

    assert safe_after(_Root(), 7, lambda a, b: seen.append((a, b)), 1, "x") == "id"
    delay, fn = fired[0]
    assert delay == 7
    fn()
    assert seen == [(1, "x")]


def test_worker_survives_dead_window():
    """窗口销毁后 Worker 的上报不得产生未捕获异常。

    关窗口时正在跑的任务会继续往回发进度/结果。如果 ``after`` 注册
    抛出没人接的异常，工作线程就会带着异常死掉——``on_done`` 和
    ``on_error`` 都收不到，界面永远卡在「执行中」。

    .. important::
       断言的是**没有未捕获异常**，而不是「线程退出了」。线程抛异常后
       照样会退出，只看 ``is_alive()`` 的话新旧代码都通过，这条测试
       等于没测。真正的信号是 ``threading.excepthook`` 有没有被触发。
    """
    import threading

    import tkinter as tk

    from bilibili_submit.ui.workers import Worker

    class _Dead:
        def after(self, *_a, **_k):
            raise tk.TclError("invalid command name 'after'")

    uncaught: list[BaseException] = []
    original = threading.excepthook
    threading.excepthook = uncaught.append
    try:
        seen: list[object] = []
        worker = Worker(_Dead())
        worker.run(
            lambda report, is_cancelled: report("进度"),
            on_progress=seen.append,
            on_error=lambda exc: seen.append(exc),
        )
        if worker._thread is not None:
            worker._thread.join(3)
    finally:
        threading.excepthook = original

    assert not uncaught, f"线程里有未捕获异常：{uncaught!r}"


# ---------- 需要 Tk ----------


def _display_available() -> bool:
    """真的试着建一个 Tk 根窗口。

    只看 ``DISPLAY`` 环境变量不够——很多环境里它被设了值，
    但 X server 根本不在（CI、容器里常见），那样 Tk() 才会暴露真相。
    """
    try:
        import tkinter as tk

        root = tk.Tk()
        root.destroy()
        return True
    except Exception:  # noqa: BLE001 - 任何失败都当作没有显示环境
        return False


needs_display = pytest.mark.skipif(
    not _display_available(), reason="无显示环境（CI 可配 Xvfb 后自动启用）"
)


@needs_display
def test_theme_apply_does_not_raise():
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        theme.apply(style)  # 不该抛
    finally:
        root.destroy()


@needs_display
def test_fluent_button_states_and_invoke():
    """自绘按钮的五态与 ttk 兼容接口。

    ``state()`` 的形状是照着 ``ttk.Widget.state()`` 抄的——现有代码和
    测试全都写 ``"disabled" in button.state()``，接口对不上就是一场
    全项目的连锁修改。
    """
    import tkinter as tk

    from bilibili_submit.ui.widgets import PrimaryButton, SecondaryButton

    root = tk.Tk()
    try:
        hits: list[str] = []
        primary = PrimaryButton(root, "开始投稿", lambda: hits.append("p"))
        secondary = SecondaryButton(root, "取消", lambda: hits.append("s"))
        # 不 map 出来事件送不到（Tk 只给可见窗口派发事件）
        primary.pack()
        secondary.pack()
        root.update()

        # 主按钮 40、次按钮 32——设计规范钉死的两个高度
        assert primary.winfo_reqheight() == theme.PRIMARY_BUTTON_HEIGHT + 4
        assert secondary.winfo_reqheight() == theme.CONTROL_HEIGHT + 4

        assert primary.state() == (), "初始没有附加状态"
        primary.state(["disabled"])
        assert "disabled" in primary.state()
        primary.invoke()
        assert hits == [], "禁用时不该触发回调"
        primary.state(["!disabled"])
        assert "disabled" not in primary.state()

        secondary.invoke()
        assert hits == ["s"]

        # configure/cget 也要能当 ttk.Button 用
        secondary.configure(text="返回", state="disabled")
        assert secondary.cget("text") == "返回"
        assert "disabled" in secondary.state()
        secondary.configure(state="normal")

        # 两字按钮不该被算成 44px 宽，最小宽度要兜住
        assert secondary.winfo_reqwidth() >= 64

        # 键盘可达：Return/空格要绑上（不直接 event_generate——
        # 无窗口管理器的 Xvfb 不派发合成键盘事件，原生控件同样收不到）
        assert primary.bind("<Return>") and primary.bind("<space>")
    finally:
        root.destroy()


@needs_display
def test_fluent_button_picks_up_dark_palette():
    """按钮颜色在创建时取自当前模式；深色模式下实心底应是深色板的强调色。"""
    import tkinter as tk

    from bilibili_submit.ui.widgets import FluentButton

    theme.set_mode("dark")
    try:
        root = tk.Tk()
        try:
            button = FluentButton(
                root, "开始投稿", lambda: None,
                variant=FluentButton.ACCENT, height=theme.PRIMARY_BUTTON_HEIGHT,
            )
            assert button.winfo_reqheight() == theme.PRIMARY_BUTTON_HEIGHT + 4
        finally:
            root.destroy()
    finally:
        theme.set_mode("light")


@needs_display
def test_all_views_build_and_switch():
    """建窗口 → 逐个切页面。

    这一条能抓出 grid 配置错误、回调签名错、样式名写错等
    只在 create_widget 时才暴露的问题。
    """
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.app import App

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        theme.apply(style)

        app = App(root)
        app.pack(fill="both", expand=True)

        assert set(app._views) == {"登录", "投稿", "批量任务", "历史", "设置"}

        for key in app._views:
            app.show(key)
            root.update_idletasks()
    finally:
        root.destroy()


@needs_display
def test_layout_survives_resizing():
    """从最小尺寸拉到全高清，各页面都不能崩。"""
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.app import App

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        theme.apply(style)
        app = App(root)
        app.pack(fill="both", expand=True)

        for width, height in (
            (theme.MIN_WIDTH, theme.MIN_HEIGHT),
            (theme.DEFAULT_WIDTH, theme.DEFAULT_HEIGHT),
            (1920, 1080),
        ):
            root.geometry(f"{width}x{height}")
            root.update_idletasks()
            for key in app._views:
                app.show(key)
                root.update_idletasks()
    finally:
        root.destroy()


@needs_display
def test_qr_draws_on_canvas():
    pytest.importorskip("qrcode")
    import tkinter as tk

    from bilibili_submit.ui.qr import draw_placeholder, draw_qr

    root = tk.Tk()
    try:
        canvas = tk.Canvas(root, width=220, height=220)
        draw_qr(canvas, "https://example.com/login?key=abc")
        assert len(canvas.find_all()) > 100, "二维码没画出来"

        draw_placeholder(canvas, "等待获取")
        assert canvas.find_all(), "占位画面应该有内容"
    finally:
        root.destroy()


@needs_display
def test_submit_button_reachable_at_min_size():
    """最小窗口下「开始投稿」必须可达。

    表单比窗口高是正常的（内容就该允许滚动），
    但必须保证：内容被包在可滚动的容器里，且滚到底后按钮完整可见。
    否则用户既看不到也点不到，界面等于废掉。
    """
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.app import App
    from bilibili_submit.ui.widgets import ScrollArea

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        theme.apply(style)
        app = App(root)
        app.pack(fill="both", expand=True)
        app.show("投稿")

        root.geometry(f"{theme.MIN_WIDTH}x{theme.MIN_HEIGHT}")
        root.update_idletasks()

        view = app._views["投稿"]

        def find_areas(widget, out):
            for child in widget.winfo_children():
                if isinstance(child, ScrollArea):
                    out.append(child)
                find_areas(child, out)

        areas: list = []
        find_areas(view, areas)
        assert areas, (
            f"投稿页内容比最小窗口高（{view.winfo_reqheight()} > {theme.MIN_HEIGHT}），"
            "必须放进 ScrollArea，否则日志区被挤没"
        )

        # 主操作**必须在滚动区之外**。放进 ScrollArea 的话，内容一长
        # 按钮就被推出屏幕，用户每次都得先滚到底才能开始投稿——
        # 这正是 ActionBar 要解决的问题。
        area = areas[0]
        bar = view._action_bar
        assert bar not in area._canvas.winfo_children(), (
            "操作条不能放进 ScrollArea：内容一长「开始投稿」就会被推出可视范围"
        )
        bar_bottom = bar.winfo_rooty() + bar.winfo_height()
        window_bottom = root.winfo_rooty() + root.winfo_height()
        assert bar_bottom <= window_bottom, (
            f"最小窗口下操作条被截断：{bar_bottom} > 窗口底 {window_bottom}"
        )

        # 滚到底后主按钮依然完整可见——它本来就没动过，
        # 这条断言是为了防止将来有人「顺手」把操作条塞回滚动区。
        area._canvas.yview_moveto(1.0)
        root.update_idletasks()

        button = bar._primary
        assert button is not None, "操作条没装上主按钮"
        canvas_top = area._canvas.winfo_rooty()
        canvas_bottom = canvas_top + area._canvas.winfo_height()
        button_top = button.winfo_rooty()
        button_bottom = button_top + button.winfo_height()
        assert button_bottom > canvas_bottom, (
            "主按钮应当位于滚动区之下（贴底），现在却在滚动区里面"
        )
    finally:
        root.destroy()


@needs_display
def test_scroll_area_shows_scrollbar_only_when_needed():
    """内容装得下时不留多余的滚动条，装不下时才出现。"""
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.widgets import ScrollArea

    root = tk.Tk()
    try:
        area = ScrollArea(root)
        area.pack(fill="both", expand=True)
        ttk.Label(area.body, text="很矮的内容").grid(row=0, column=0)
        root.geometry("400x400")
        root.update_idletasks()
        assert not area._overflowing(), "矮内容不该溢出"

        for index in range(40):
            ttk.Label(area.body, text=f"第 {index} 行").grid(row=index + 1, column=0)
        root.geometry("400x200")
        root.update_idletasks()
        assert area._overflowing(), "高内容应该溢出"
    finally:
        root.destroy()


@needs_display
def test_log_console_append_and_clear():
    import tkinter as tk

    from bilibili_submit.ui.widgets import LogConsole

    root = tk.Tk()
    try:
        log = LogConsole(root, height=4)
        log.pack()
        log.append("第一行")
        log.append("第二行")
        assert "第二行" in log._text.get("1.0", "end")
        log.clear()
        assert log._text.get("1.0", "end").strip() == ""
    finally:
        root.destroy()


@needs_display
def test_form_row_controls_land_in_distinct_rows():
    """表单控件必须各占一行，不能全叠在第一个单元格里。

    Tk 的 ``grid()`` 始终作用于 ``widget.master``：如果控件是用外层
    frame 当父容器建的，再塞进 FormRow，它们会被排到外层同一个格子，
    界面表现就是「所有输入框糊在第一行」。这个 bug 只在真正创建控件
    并量出实际位置时才暴露，所以必须真跑 Tk。
    """
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.widgets import FormRow

    root = tk.Tk()
    try:
        form = ttk.Frame(root)
        form.pack(fill="both", expand=True)
        form.columnconfigure(0, weight=1)

        rows = []
        for index in range(3):
            row = FormRow(form, f"字段{index}")
            row.grid(row=index, column=0, sticky="ew")
            row.add(ttk.Entry)
            rows.append(row)

        root.update_idletasks()

        tops = {entry.winfo_rooty() for row in rows
                for entry in row.body.winfo_children()}
        assert len(tops) == 3, f"三个输入框的纵坐标应各不相同，实际={tops}"
    finally:
        root.destroy()


@needs_display
def test_upload_view_inputs_do_not_overlap():
    """投稿页的输入框不能互相压在同一行。"""
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.app import App

    def collect(widget, out):
        """递归找出所有输入控件。层级会随布局调整而变，别写死。"""
        for child in widget.winfo_children():
            if child.winfo_class() in ("TEntry", "TCombobox"):
                out.append(child)
            collect(child, out)

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        theme.apply(style)
        app = App(root)
        app.pack(fill="both", expand=True)
        app.show("投稿")
        root.geometry(f"{theme.DEFAULT_WIDTH}x{theme.DEFAULT_HEIGHT}")
        root.update_idletasks()

        found: list = []
        collect(app._views["投稿"], found)
        tops = [w.winfo_rooty() for w in found]
        assert len(tops) >= 5, f"应至少有 5 个输入控件，实际 {len(tops)}"
        assert len(set(tops)) == len(tops), f"输入框纵坐标重复，发生了重叠: {tops}"
    finally:
        root.destroy()


@needs_display
@pytest.mark.parametrize(
    ("content", "expect_problem", "expect_placeholder"),
    [
        (None, False, "暂无投稿历史"),          # 文件不存在
        ("", False, "暂无投稿历史"),             # 空文件
        ("{ broken", True, "读取失败"),          # 损坏的 JSON
        ('{"a": 1}', True, "读取失败"),          # 顶层不是列表
    ],
)
def test_history_view_reports_problems(tmp_path, monkeypatch, content, expect_problem,
                                       expect_placeholder):
    """历史页要在「没投过稿」和「文件坏了」之间给出不同提示。

    两者都显示空列表的话，用户会以为程序把记录弄丢了。
    """
    import json
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit import scheduler
    from bilibili_submit.ui.app import App

    history_file = tmp_path / "history.json"
    if content is not None:
        history_file.write_text(content, encoding="utf-8")
    monkeypatch.setattr(scheduler, "DEFAULT_HISTORY_FILE", str(history_file))
    # views.history 在 import 时就把路径绑进默认值了，这里要一起改
    monkeypatch.setattr(
        "bilibili_submit.ui.views.history.DEFAULT_HISTORY_FILE", str(history_file)
    )

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        theme.apply(style)
        app = App(root)
        app.pack(fill="both", expand=True)
        view = app._views["历史"]
        view.refresh()
        root.update_idletasks()

        log = view._log._text.get("1.0", "end")
        labels = [
            child.cget("text")
            for child in view._placeholder.winfo_children()
            if isinstance(child, ttk.Label)
        ]
        assert labels, "占位文案应存在"
        if expect_problem:
            assert "⚠" in log, f"应显示警告，实际日志: {log.strip()!r}"
            assert expect_placeholder in labels[0]
            assert view._placeholder.winfo_ismapped(), "出错时应显示占位说明"
            assert not view._tree.winfo_ismapped(), "出错时不该显示空列表"
        else:
            assert "⚠" not in log, f"正常状态不该报警告: {log.strip()!r}"
            assert labels[0] == "暂无投稿历史"
            assert view._placeholder.winfo_ismapped()
        del json
    finally:
        root.destroy()


@needs_display
def test_history_view_shows_list_when_entries_exist(tmp_path, monkeypatch):
    """有记录时显示列表、隐藏占位；损坏后恢复也要正确切换。"""
    import json
    import time
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit import scheduler
    from bilibili_submit.ui.app import App

    history_file = tmp_path / "history.json"
    monkeypatch.setattr(scheduler, "DEFAULT_HISTORY_FILE", str(history_file))
    monkeypatch.setattr(
        "bilibili_submit.ui.views.history.DEFAULT_HISTORY_FILE", str(history_file)
    )

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        theme.apply(style)
        app = App(root)
        app.pack(fill="both", expand=True)
        view = app._views["历史"]

        history_file.write_text(
            json.dumps(
                [{"name": f"任务{i}", "bvid": f"BV{i}", "time": time.time() - i * 60}
                 for i in range(3)],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        view.refresh()
        root.update_idletasks()
        assert view._tree.winfo_ismapped(), "有记录时应显示列表"
        assert not view._placeholder.winfo_ismapped(), "有记录时应隐藏占位"
        assert len(view._tree.get_children()) == 3

        # 切到损坏 → 占位应重新出现
        history_file.write_text("{ broken", encoding="utf-8")
        view.refresh()
        root.update_idletasks()
        assert view._placeholder.winfo_ismapped(), "损坏后应重新显示占位"
        assert not view._tree.winfo_ismapped()

        # 再恢复正常 → 占位必须让位给列表（曾经隐藏过就不会再 grid 回来）
        history_file.write_text(
            json.dumps([{"name": "x", "bvid": "BVx", "time": time.time()}]),
            encoding="utf-8",
        )
        view.refresh()
        root.update_idletasks()
        assert view._tree.winfo_ismapped(), "恢复后列表应重新显示"
        assert not view._placeholder.winfo_ismapped(), "恢复后占位应重新隐藏"
    finally:
        root.destroy()


@needs_display
def test_history_log_does_not_accumulate_on_refresh(tmp_path, monkeypatch):
    """反复切页会反复调 refresh，日志不该一行行叠起来。"""
    import json
    import time
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit import scheduler
    from bilibili_submit.ui.app import App

    history_file = tmp_path / "history.json"
    history_file.write_text(
        json.dumps([{"name": "a", "bvid": "BV1", "time": time.time()}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler, "DEFAULT_HISTORY_FILE", str(history_file))
    monkeypatch.setattr(
        "bilibili_submit.ui.views.history.DEFAULT_HISTORY_FILE", str(history_file)
    )

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        theme.apply(style)
        app = App(root)
        app.pack(fill="both", expand=True)
        view = app._views["历史"]
        for _ in range(4):
            view.refresh()
        root.update_idletasks()
        lines = [
            line for line in view._log._text.get("1.0", "end").splitlines() if line.strip()
        ]
        assert len(lines) == 1, f"刷新 4 次后日志应只有 1 行，实际 {len(lines)}: {lines}"
    finally:
        root.destroy()


def _build_login_view(root):
    """建一个带主题的 App，返回登录页视图。"""
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.app import App

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    theme.apply(style)

    app = App(root)
    app.pack(fill="both", expand=True)
    return app, app._views["登录"]


@needs_display
def test_login_view_draws_qr_via_main_thread_channel(monkeypatch):
    """二维码必须经进度通道回主线程绘制，不能在工作线程里直接碰 Tk。

    工作线程里调 ``after`` 在真 mainloop 下多数能work，
    但违反了 workers.py 定的「业务代码不操心线程」原则，
    且窗口销毁等边界下会抛 TclError。这里钉住这条通道。
    """
    import tkinter as tk

    from bilibili_submit.ui.views import login as login_mod

    root = tk.Tk()
    try:
        _app, view = _build_login_view(root)

        drawn = {}
        monkeypatch.setattr(
            login_mod, "draw_qr", lambda canvas, url: drawn.__setitem__("url", url)
        )

        # 直接喂哨兵，验证 _on_progress 会转成 _show_qr
        view._qr_url = "https://example.test/qr"
        view._on_progress(view._PENDING_QR)
        root.update_idletasks()

        assert drawn.get("url") == "https://example.test/qr", (
            "收到 _PENDING_QR 后应调用 draw_qr 画二维码"
        )
        # 链接也要显示出来，方便扫码不便时手动打开
        assert "example.test" in view._link.cget("text")
    finally:
        root.destroy()


@needs_display
def test_login_progress_channel_does_not_leak_sentinel():
    """哨兵不该被当成文字打进日志，否则用户会看到 <pending-qr>。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        _app, view = _build_login_view(root)
        view._qr_url = "https://example.test/qr"
        view._log.clear()
        view._on_progress(view._PENDING_QR)
        text = view._log._text.get("1.0", "end")
        assert "pending-qr" not in text, f"哨兵泄漏进了日志：{text!r}"
    finally:
        root.destroy()


@needs_display
def test_login_view_uses_shared_session_factory(monkeypatch):
    """登录流程必须用 auth.new_session()，裸 Session 会被风控 412。"""
    import tkinter as tk

    from bilibili_submit.auth import QrCode
    from bilibili_submit.ui.views import login as login_mod

    root = tk.Tk()
    try:
        _app, view = _build_login_view(root)

        captured = {}

        def fake_new_session(proxy=None):
            captured["proxy"] = proxy
            return object()

        monkeypatch.setattr(login_mod, "new_session", fake_new_session)
        monkeypatch.setattr(
            login_mod,
            "request_qrcode",
            lambda session: QrCode(key="k", url="https://example.test/qr"),
        )

        # 轮询直接报错，让流程在拿到二维码后立刻结束
        def boom(*_a, **_kw):
            raise login_mod.BiliError("停止轮询")

        monkeypatch.setattr(login_mod, "poll_qrcode", boom)

        messages = []
        with pytest.raises(login_mod.BiliError):
            view._login_flow(messages.append, lambda: False)

        assert "proxy" in captured, "登录流程应通过 new_session() 构造会话"
        # 二维码就绪信号必须发出去，否则界面永远停在占位图
        assert view._PENDING_QR in messages, (
            "拿到二维码后应通过 report 发哨兵，触发主线程绘制"
        )
    finally:
        root.destroy()


@needs_display
def test_login_error_shows_hint_in_log():
    """风控等异常自带 hint，日志里要能看到排查方向而不是只有错误码。"""
    import tkinter as tk

    from bilibili_submit.exceptions import NetworkError

    root = tk.Tk()
    try:
        _app, view = _build_login_view(root)
        view._log.clear()
        view._on_error(
            NetworkError("获取二维码失败: HTTP 412", hint="被风控拦截，换代理试试")
        )

        text = view._log._text.get("1.0", "end")
        assert "412" in text
        assert "换代理" in text, f"hint 未显示：{text!r}"
        assert "失败" in view._status.cget("text"), "状态标签也要提示失败"
        # BiliError._compose 已把 hint 拼进 str(exc)，别再重复打一遍
        assert text.count("换代理") == 1, f"hint 重复显示了：{text!r}"
    finally:
        root.destroy()


@needs_display
def test_login_success_unlocks_buttons_even_if_save_fails(monkeypatch):
    """cookie 写盘失败也不能把登录页锁死。

    ``_on_done`` 是排进 Tk 事件循环执行的，抛出的异常没人接。原先
    ``save_cookies()`` 排在 ``_set_busy(False)`` **之前**，路径不可写时
    （设置页能把 cookie 路径指到任意位置）就会：开始按钮永久 disabled、
    取消亮着但线程已死、状态标签还停在旧文案——用户看着像登录成功了，
    却既不能重试也不能取消，只能重启进程。
    """
    import tkinter as tk

    from bilibili_submit.ui.views import login as login_mod

    root = tk.Tk()
    try:
        _app, view = _build_login_view(root)
        view._set_busy(True)
        view._log.clear()

        def unwritable(*_a, **_k):
            raise OSError("Read-only file system")

        monkeypatch.setattr(login_mod, "save_cookies", unwritable)

        view._on_done({"SESSDATA": "x"})
        root.update_idletasks()

        assert "disabled" not in view._start_button.state(), (
            "写盘失败后开始按钮必须恢复，否则登录页永久锁死"
        )
        assert "disabled" in view._cancel_button.state(), "取消按钮应回到禁用"
        text = view._log._text.get("1.0", "end")
        assert "保存失败" in view._status.cget("text"), "状态要说明发生了什么"
        assert "Read-only" in text or "写入" in text, f"日志要给出原因：{text!r}"
    finally:
        root.destroy()


@needs_display
def test_login_status_refresh_failure_does_not_raise(monkeypatch):
    """状态栏刷新失败不该把整个回调炸掉。

    ``refresh_status`` 会读 cookie 文件，文件损坏时抛 ``BiliError``。
    登录刚成功却因为读不了 cookie 而炸掉回调，用户会看到「登录成功」
    但界面半推半就地卡住。
    """
    import tkinter as tk

    from bilibili_submit.exceptions import BiliError

    root = tk.Tk()
    try:
        _app, view = _build_login_view(root)
        view._log.clear()

        def boom():
            raise BiliError("cookie 文件损坏")

        monkeypatch.setattr(view.app, "refresh_status", boom)
        view._refresh_app_status()          # 不应抛出

        assert "状态栏刷新失败" in view._log._text.get("1.0", "end")
    finally:
        root.destroy()


@needs_display
def test_collapsible_hides_its_body_when_closed():
    """收起时内容真的不占位——否则「把高度还给列表」就是句空话。"""
    import tkinter as tk

    from bilibili_submit.ui.widgets import Collapsible

    root = tk.Tk()
    try:
        box = Collapsible(root, "更多设置", "互动与音质")
        box.pack(fill="x")
        row = tk.Frame(box.body)
        row.pack()

        assert not box.opened
        root.update()
        assert str(row.winfo_ismapped()) != "1"

        box.set_opened(True)
        assert box.opened
        root.update()
        assert str(row.winfo_ismapped()) == "1"

        # 关键一步：展开过再收起，内容必须重新藏起来。
        # 只测「初始收起」抓不住 grid_remove 漏写——那种情况下 body
        # 压根没被 grid 过，看着也是不占位。
        box.set_opened(False)
        root.update()
        assert str(row.winfo_ismapped()) != "1"
    finally:
        root.destroy()


@needs_display
def test_collapsible_click_toggles_and_fires_callback():
    """点标题栏要能展开；重复设同一个值不重复触发回调。"""
    import tkinter as tk

    from bilibili_submit.ui.widgets import Collapsible

    root = tk.Tk()
    try:
        seen: list[bool] = []
        box = Collapsible(root, "更多设置", on_toggle=seen.append)
        box.pack(fill="x")

        box._on_click(None)
        assert box.opened
        box._on_click(None)
        assert not box.opened
        assert seen == [True, False]

        # 已经是收起状态再设一次，不该有多余回调（否则会触发无谓的存盘）
        box.set_opened(False)
        assert seen == [True, False]
    finally:
        root.destroy()


@needs_display
def test_collapsible_hint_survives_when_collapsed():
    """提示文字是收起后唯一还能看见的状态，必须读得到。"""
    import tkinter as tk

    from bilibili_submit.ui.widgets import Collapsible

    root = tk.Tk()
    try:
        box = Collapsible(root, "更多设置")
        box.pack()
        assert box.hint == ""
        box.set_hint("杜比、Hi-Res")
        assert box.hint == "杜比、Hi-Res"
    finally:
        root.destroy()


# ---------- 风格纪律 ----------


def test_ui_modules_do_not_hardcode_colors():
    """界面层不许出现颜色字面量，颜色只能来自 theme。

    只扫颜色不扫间距：``pady=4`` 这种小数字很难和「恰好等于 PAD_XS」
    的合法写法区分开，误报会让人直接把这条测试删掉。而颜色是真出过
    问题的——投稿页写过硬编码间距、两页的灰深浅不一样，改主色时要
    翻遍所有文件才知道漏了哪。
    """
    import re
    from pathlib import Path

    ui_dir = Path(__file__).resolve().parent.parent / "bilibili_submit" / "ui"
    pattern = re.compile(r"#[0-9A-Fa-f]{6}\b")
    offenders = []
    for path in sorted(ui_dir.rglob("*.py")):
        if path.name == "theme.py":
            continue  # 主题自己就是颜色的来源
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{lineno}")
    assert not offenders, f"界面层出现硬编码颜色，应改用 theme: {offenders}"


def test_two_pages_use_the_same_option_labels():
    """投稿页与批量页用同一组开关标签。

    两个页面各自写一遍标签文字的话，改一个忘一个就是「这个页叫杜比、
    那个页叫杜比音效」，用户会以为是两个不同的功能。
    """
    from bilibili_submit.ui.views import tasks, upload

    assert tasks.OptionSwitches is upload.OptionSwitches


@needs_display
def test_upload_page_has_the_same_option_switches():
    """投稿页的更多设置与批量页是同一组开关，不是另写一份。"""
    import tkinter as tk

    from bilibili_submit.ui.views.upload import UploadView

    root = tk.Tk()
    try:
        app = type("App", (), {"ctx": None})()
        view = UploadView(root, app)
        flags = view._option_switches.flags
        assert set(flags) == {
            "close_danmu", "close_reply", "selection_reply", "dolby", "hires",
        }
        # 默认全关：投稿不该替用户改变互动设置
        assert not any(flags.values())
        assert not view._more.opened
    finally:
        root.destroy()


@needs_display
def test_upload_page_collects_the_new_options(tmp_path):
    """投稿页勾的开关要进到 TaskConfig，否则投出去没效果。"""
    import tkinter as tk

    from bilibili_submit.ui.views.upload import UploadView

    root = tk.Tk()
    try:
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        app = type("App", (), {"ctx": None})()
        view = UploadView(root, app)
        view._file_var.set(str(video))
        view._option_switches.set_flags(dolby=True, close_danmu=True)
        task = view._collect()
        assert task.dolby == 1
        assert task.up_close_danmu is True
        assert task.hires == 0
    finally:
        root.destroy()
