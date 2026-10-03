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
        assert "YaHei" in theme.FAMILY
    elif sys.platform == "darwin":
        assert "PingFang" in theme.FAMILY


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
    """下拉框选项是「21 - 日常」这种，要能取回分区号。"""
    from bilibili_submit.ui.views.upload import _parse_tid

    assert _parse_tid("21 - 日常") == 21
    assert _parse_tid("171 - 电子竞技") == 171
    # 无法解析时回退默认值，而不是抛异常打断表单填写
    assert _parse_tid("") == 21
    assert _parse_tid("乱七八糟") == 21


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


def test_worker_cancel_is_cooperative():
    """取消只是置标志位，任务自己检查——不能强杀线程。"""
    from bilibili_submit.ui.workers import Cancelled, Worker

    assert issubclass(Cancelled, Exception)
    assert hasattr(Worker, "cancel")
    assert hasattr(Worker, "run")


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
            "必须放进 ScrollArea，否则底部按钮不可达"
        )

        area = areas[0]
        # 滚到底后再量：此时按钮应当完整落在可视区内
        area._canvas.yview_moveto(1.0)
        root.update_idletasks()

        button = view._submit_button
        canvas_top = area._canvas.winfo_rooty()
        canvas_bottom = canvas_top + area._canvas.winfo_height()
        button_top = button.winfo_rooty()
        button_bottom = button_top + button.winfo_height()
        assert button_top >= canvas_top and button_bottom <= canvas_bottom, (
            f"滚到底后「开始投稿」仍不在可视区内："
            f"按钮 {button_top}~{button_bottom} vs 可视区 {canvas_top}~{canvas_bottom}"
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
