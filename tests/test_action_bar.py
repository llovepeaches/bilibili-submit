"""主操作可用性契约测试。

这一组测的是本次界面重做的**核心承诺**：

1. 主按钮永远在同一个位置（底部操作条），不随内容滚动或折叠开合移动
2. 条件不满足时**就地禁用并说明原因**，不让用户点了才知道不行
3. 「将要发生什么」在提交前就写在界面上

第2 条是最容易退化的一条——把 ``block()`` 改成只禁用不解释，界面看起来
完全正常（按钮变灰了），但用户面对一个说不出为什么的灰按钮。
所以每条行为都有对应的变异测试守着。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.ui import theme  # noqa: E402
from conftest import needs_display  # noqa: E402 - 由 tests/conftest.py 统一提供


# ---------- 纯逻辑：截断规则 ----------


def test_reason_gets_truncated_to_keep_the_button_visible():
    """原因太长要截断——操作条是固定高度的一行。

    按钮比原因重要：写长了把「开始投稿」挤出可视区就是本末倒置。
    """
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        from bilibili_submit.ui.widgets import ActionBar

        bar = ActionBar(root)
        long_text = "尚未登录 · 请先到「登录」页扫码登录，登录后回到这里就能开始投稿了"
        bar.block(long_text)
        shown = bar._reason.cget("text")
        assert len(shown) < len(long_text), "长原因必须截断"
        assert shown.endswith("…"), "截断后要有省略号，让人知道还有内容"
    finally:
        root.destroy()


def test_short_reason_is_kept_verbatim():
    """短原因原样显示，不要多动。"""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        from bilibili_submit.ui.widgets import ActionBar

        bar = ActionBar(root)
        bar.block("尚未登录")
        assert bar._reason.cget("text") == "尚未登录"
    finally:
        root.destroy()


# ---------- 需要 Tk：禁用必须带原因 ----------


@needs_display
def test_block_without_reason_keeps_the_reason_hidden():
    """``block("")`` 禁用但不解释——这是**故意**允许的（组件不该管）。

    但它绝不能顺手把上一条原因留着：控件状态变了、文案还停在
    「尚未登录」是最糟的一种错——比什么都不说更误导。
    """
    import tkinter as tk

    root = tk.Tk()
    try:
        from bilibili_submit.ui.widgets import ActionBar

        bar = ActionBar(root)
        bar.set_primary("开始投稿", lambda: None)
        bar.block("尚未登录")
        assert bar._reason.cget("text") == "尚未登录"

        bar.block("")
        assert bar._reason.cget("text") == "", "block('') 必须清掉旧原因"
        assert not bar._reason.winfo_ismapped(), "没原因时不该占位"
    finally:
        root.destroy()


@needs_display
def test_block_disables_primary_and_unblock_restores_it():
    import tkinter as tk

    root = tk.Tk()
    try:
        from bilibili_submit.ui.widgets import ActionBar

        bar = ActionBar(root)
        bar.set_primary("开始投稿", lambda: None)
        bar.block("缺文件")
        assert "disabled" in bar._primary.state()

        bar.unblock()
        assert "disabled" not in bar._primary.state()
        assert not bar._reason.winfo_ismapped(), "解除禁用后原因要收起来"
    finally:
        root.destroy()


@needs_display
def test_primary_and_secondary_do_not_overlap():
    """主按钮和次按钮必须各占一列。

    曾经把次按钮也 grid 到主按钮那一列（靠 sticky 区分），结果两者
    完全重叠——粉底主按钮被压在下面看不见，而测试是绿的。
    """
    import tkinter as tk

    root = tk.Tk()
    try:
        from bilibili_submit.ui.widgets import ActionBar

        bar = ActionBar(root)
        bar.set_primary("开始投稿", lambda: None,
                        secondary_text="仅预览", secondary_command=lambda: None)
        root.update_idletasks()
        primary = bar._primary
        secondary = bar.secondary
        assert primary is not None and secondary is not None
        assert primary.winfo_x() > secondary.winfo_x(), (
            "主按钮必须在次按钮右侧（主操作在视觉最远处，误点成本最低）"
        )
        # 横向不重叠
        p_left, p_right = primary.winfo_x(), primary.winfo_x() + primary.winfo_width()
        s_left, s_right = secondary.winfo_x(), secondary.winfo_x() + secondary.winfo_width()
        assert p_left >= s_right, f"主按钮({p_left}~{p_right})和次按钮({s_left}~{s_right})重叠了"
    finally:
        root.destroy()


@needs_display
def test_set_busy_restores_the_original_button_label():
    """运行结束后要变回原来的文案，不能硬编码「开始投稿」。

    批量页的主按钮可能叫别的；硬编码回去等于把它改名了。
    """
    import tkinter as tk

    root = tk.Tk()
    try:
        from bilibili_submit.ui.widgets import ActionBar

        bar = ActionBar(root)
        bar.set_primary("全部投稿", lambda: None)
        bar.set_busy(True, cancel=lambda: None)
        assert bar._primary.cget("text") == "取消"

        bar.set_busy(False)
        assert bar._primary.cget("text") == "全部投稿", "收尾时必须变回原文案"
    finally:
        root.destroy()


@needs_display
def test_set_busy_uses_the_given_cancel_label():
    """扫描目录时叫「取消」会让人以为要放弃整批投稿，所以文案要能传。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        from bilibili_submit.ui.widgets import ActionBar

        bar = ActionBar(root)
        bar.set_primary("开始投稿", lambda: None)
        bar.set_busy(True, cancel=lambda: None,
                     cancel_text="停止扫描", busy_title="正在扫描文件夹")
        assert bar._primary.cget("text") == "停止扫描"
        assert bar._title.cget("text") == "正在扫描文件夹"
    finally:
        root.destroy()


# ---------- 需要 Tk：投稿页的可用性 ----------


@needs_display
def test_upload_blocks_primary_when_not_logged_in(tmp_path):
    """**未登录时主按钮必须是禁用的，且说明原因。**

    这是「死后告知」的反面：原来是点了按钮、再在日志里看到「尚未登录」，
    用户白点一次，还可能以为自己哪里填错了。
    """
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.views.upload import UploadView

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        theme.apply(style)
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")

        class Ctx:
            logged_in = False

        app = type("App", (), {"ctx": Ctx()})()
        view = UploadView(root, app)
        view._file_var.set(str(video))
        view._refresh_action_bar()
        root.update_idletasks()

        bar = view._action_bar
        assert "disabled" in bar._primary.state(), "未登录时主按钮必须禁用"
        assert "登录" in bar._reason.cget("text"), (
            f"禁用时必须说明原因，现在写的是「{bar._reason.cget('text')}」"
        )
    finally:
        root.destroy()


@needs_display
def test_upload_unblocks_when_everything_is_ready(tmp_path):
    """登录 + 选了文件 → 主按钮可点，且摘要说清「将要发生什么」。"""
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.views.upload import UploadView

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        theme.apply(style)
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")

        class Ctx:
            logged_in = True

        app = type("App", (), {"ctx": Ctx()})()
        view = UploadView(root, app)
        view._file_var.set(str(video))
        view._title_var.set("测试标题")
        view._refresh_action_bar()
        root.update_idletasks()

        bar = view._action_bar
        assert "disabled" not in bar._primary.state(), "条件齐了主按钮必须可点"
        summary = bar._summary.cget("text")
        assert "测试标题" not in summary or "a.mp4" in summary
        assert "a.mp4" in summary, f"摘要要点出是哪个文件：{summary}"
        assert "自制" in summary, f"摘要要点出投稿类型：{summary}"
        assert not bar._reason.winfo_ismapped(), "没有阻塞原因就别占位"
    finally:
        root.destroy()


@needs_display
def test_upload_preview_needs_a_file_even_while_logged_out(tmp_path):
    """空表单时「仅预览」也必须禁用。

    预览不需要登录（走``need_login=False``），但**同样得有文件**。
    主按钮被禁用、次按钮却亮着是最坑的一种组合：用户会以为「那点
    预览总可以吧」，点下去只得到一句报错。亮的按钮必须真的能按。
    """
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.views.upload import UploadView

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        theme.apply(style)

        class Ctx:
            logged_in = True

        app = type("App", (), {"ctx": Ctx()})()
        view = UploadView(root, app)
        view._refresh_action_bar()
        root.update_idletasks()

        bar = view._action_bar
        assert "disabled" in bar._primary.state(), "没选文件主按钮必须禁用"
        preview = bar.secondary
        assert preview is not None, "上传页应当有「仅预览」次按钮"
        assert "disabled" in preview.state(), (
            "没选文件时「仅预览」也必须禁用——亮着的按钮必须真的能按"
        )

        # 选了文件之后，两个按钮都要活
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        view._file_var.set(str(video))
        view._refresh_action_bar()
        root.update_idletasks()
        assert "disabled" not in preview.state(), "选了文件「仅预览」应该可用"
    finally:
        root.destroy()


@needs_display
def test_upload_preview_stays_enabled_when_logged_out(tmp_path):
    """未登录时「仅预览」仍可用——它的门槛只有文件，没有登录。"""
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.views.upload import UploadView

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        theme.apply(style)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")

        class Ctx:
            logged_in = False

        app = type("App", (), {"ctx": Ctx()})()
        view = UploadView(root, app)
        view._file_var.set(str(video))
        view._refresh_action_bar()
        root.update_idletasks()

        bar = view._action_bar
        assert "disabled" in bar._primary.state(), "未登录主按钮必须禁用"
        assert "尚未登录" in bar._reason.cget("text"), "禁用必须说明原因"
        assert "disabled" not in bar.secondary.state(), (
            "预览不需要登录，不该被登录状态连坐"
        )
    finally:
        root.destroy()


@needs_display
def test_upload_summary_follows_the_form(tmp_path):
    """填了标题要反映到摘要里——这是「提交前不回头核对」的前提。"""
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.views.upload import UploadView

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        theme.apply(style)
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")

        class Ctx:
            logged_in = True

        app = type("App", (), {"ctx": Ctx()})()
        view = UploadView(root, app)
        view._file_var.set(str(video))
        root.update_idletasks()
        assert "clip.mp4" in view._action_bar._summary.cget("text")

        # 切成转载：摘要要跟着变，否则显示的还是旧计划
        view._copyright_var.set("转载")
        root.update_idletasks()
        assert "转载" in view._action_bar._summary.cget("text"), (
            "切换投稿类型后摘要必须更新"
        )
    finally:
        root.destroy()


@needs_display
def test_upload_source_row_marked_required():
    """转载来源是必填项——标签上要有星号，让用户填之前就知道。"""
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui.views.upload import UploadView

    root = tk.Tk()
    try:
        style = ttk.Style(root)
        theme.apply(style)
        app = type("App", (), {"ctx": None})()
        view = UploadView(root, app)
        labels = []

        def collect_labels(widget):
            """星号 label 嵌在字段格的标题行里，递归收集才找得全。"""
            for child in widget.winfo_children():
                if child.winfo_class() in ("TLabel", "Label"):
                    labels.append(child.cget("text"))
                collect_labels(child)

        collect_labels(view._source_row)
        assert any("*" in text for text in labels), (
            f"转载来源必须有必填标记，实际标签：{labels}"
        )
    finally:
        root.destroy()
