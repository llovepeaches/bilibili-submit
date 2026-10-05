"""投稿类型（自制 / 转载）测试。

底层其实早就支持 ``copyright``——``metadata`` 有字段、``config`` 有
配置项、``cli`` 有 ``--copyright``。缺的是**界面上根本选不到**，
所以这一组测的是「点了之后能不能一路走到 TaskConfig」这条链路。

转载有个额外的坑：``copyright=2`` 时 B 站要求 ``source``，缺了服务端
只回一句 21004。所以界面必须当场拦住——批量投稿时尤其明显，否则
N 个稿件逐个失败，列表一片红却看不出是同一个原因。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.ui.views.upload import (  # noqa: E402
    COPYRIGHT_OPTIONS,
    copyright_option,
    parse_copyright,
)

SELF_MADE, REPRINT = COPYRIGHT_OPTIONS


# ---------- 纯逻辑 ----------


def test_copyright_parsing():
    """下拉里是中文，值是 B 站的 1/2——这层翻译只在这一处。"""
    assert parse_copyright(SELF_MADE) == 1
    assert parse_copyright(REPRINT) == 2


def test_unknown_copyright_falls_back_to_self_made():
    """认不出来时回落自制，不回落转载。

    方向是刻意的：自制不要求填来源，不会让投稿当场失败；反过来认不出
    就当转载，用户会撞上「缺 source」被拒稿，却分不清是自己没选还是
    程序弄错了。
    """
    assert parse_copyright("") == 1
    assert parse_copyright("转载 ") == 2, "前后空格要认"
    assert parse_copyright("自制") == 1
    assert parse_copyright("乱写的") == 1
    assert parse_copyright(None) == 1


def test_copyright_option_roundtrip():
    """存进偏好的是数字，回填下拉要变回中文。"""
    assert copyright_option(1) == SELF_MADE
    assert copyright_option(2) == REPRINT
    assert copyright_option(9) == SELF_MADE, "只认 2，其余按自制显示"
    for value in (1, 2):
        assert parse_copyright(copyright_option(value)) == value


def test_build_archive_meta_carries_copyright():
    """配置里的 copyright/source 要真的进到稿件元数据。"""
    from bilibili_submit.config import TaskConfig
    from bilibili_submit.scheduler import build_archive_meta

    meta = build_archive_meta(
        TaskConfig(name="a", copyright=2, source="https://example.com/origin"),
        [{"filename": "f", "title": "t", "desc": ""}],
    )
    assert meta.copyright == 2
    assert meta.source == "https://example.com/origin"


def test_shared_values_apply_carries_copyright():
    """批量页顶部选的类型要套到这一批每个任务上。"""
    from bilibili_submit.config import TaskConfig
    from bilibili_submit.ui.views.tasks import SharedSubmitValues

    task = TaskConfig(name="a", file="a.mp4")

    out = SharedSubmitValues(tid=21, copyright=2, source="https://example.com/x")
    applied = out.apply(task)
    assert applied.copyright == 2
    assert applied.source == "https://example.com/x"

    # 自制时不带来源：留着上次填的转载地址，哪天切回转载就会把一个
    # 早就不相干的出处投上去
    plain = SharedSubmitValues(tid=21, copyright=1, source="  ").apply(task)
    assert plain.copyright == 1
    assert plain.source is None


# ---------- 需要 Tk ----------


def _display_available() -> bool:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.destroy()
        return True
    except Exception:  # noqa: BLE001 - 任何失败都当作没有显示环境
        return False


needs_display = pytest.mark.skipif(
    not _display_available(), reason="无显示环境（CI 可配 Xvfb 后自动启用）"
)


def _upload_view(root, tmp_path):
    from tkinter import ttk

    from bilibili_submit.ui import theme
    from bilibili_submit.ui.views.upload import UploadView

    style = ttk.Style(root)
    theme.apply(style)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    app = type("App", (), {"ctx": None})()
    view = UploadView(root, app)
    view._file_var.set(str(video))
    return view


@needs_display
def test_upload_page_hides_source_until_reprint(tmp_path):
    """默认自制时「转载来源」不该出现在表单里。

    三条路径一起测：初始收起 → 选转载展开 → 切回自制再收起。只测初始
    状态抓不住漏写 ``grid_remove()``——那一行压根没被 grid 过，看着也
    是不显示的。"切回自制"才逼着代码真的把它摘掉。
    """
    import tkinter as tk

    root = tk.Tk()
    try:
        view = _upload_view(root, tmp_path)
        root.update_idletasks()
        assert not view._source_row.winfo_ismapped(), "默认自制，来源行不该显示"

        view._copyright_var.set(REPRINT)
        view._sync_source_row()
        root.update_idletasks()
        assert view._source_row.winfo_ismapped(), "选了转载就该能填来源"

        view._copyright_var.set(SELF_MADE)
        view._sync_source_row()
        root.update_idletasks()
        assert not view._source_row.winfo_ismapped(), "切回自制要把来源行收起来"
    finally:
        root.destroy()


@needs_display
def test_upload_page_collects_reprint(tmp_path):
    """选转载 + 填来源 → 任务配置里两个字段都在。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        view = _upload_view(root, tmp_path)
        view._copyright_var.set(REPRINT)
        view._source_var.set("https://example.com/origin")
        task = view._collect()
        assert task.copyright == 2
        assert task.source == "https://example.com/origin"
    finally:
        root.destroy()


@needs_display
def test_upload_page_defaults_to_self_made(tmp_path):
    """不碰类型下拉时投出去的是自制，source 为空。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        view = _upload_view(root, tmp_path)
        task = view._collect()
        assert task.copyright == 1
        assert task.source is None
    finally:
        root.destroy()


@needs_display
def test_upload_page_rejects_reprint_without_source(tmp_path):
    """选了转载却不填来源：当场拦住，别让用户等服务端一句 21004。"""
    import tkinter as tk

    from bilibili_submit.exceptions import BiliError

    root = tk.Tk()
    try:
        view = _upload_view(root, tmp_path)
        view._copyright_var.set(REPRINT)
        view._source_var.set("   ")
        with pytest.raises(BiliError, match="转载来源"):
            view._collect()
    finally:
        root.destroy()


def _tasks_view(root, tmp_path):
    from tkinter import ttk

    from bilibili_submit.ui import theme
    from bilibili_submit.ui.app import App

    style = ttk.Style(root)
    theme.apply(style)
    app = App(root)
    app.pack(fill="both", expand=True)
    view = app._views["批量任务"]
    view._state_file = tmp_path / "ui-state.json"
    return app, view


@needs_display
def test_batch_page_hides_source_until_reprint(tmp_path):
    """批量页同样按类型显隐来源行，两页行为一致。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        _app, view = _tasks_view(root, tmp_path)
        root.update_idletasks()
        # 用 grid_info 而不是 winfo_ismapped：批量页的来源行在收起的
        # 折叠区里，无论显隐与否 ismapped 都是 0。要测的是
        # 「这一行有没有被挂到网格上」，那才是显隐逻辑本身
        assert not view._source_row.grid_info()

        view._copyright_var.set(REPRINT)
        view._sync_source_row()
        root.update_idletasks()
        assert view._source_row.grid_info(), "选了转载就该能填来源"

        view._copyright_var.set(SELF_MADE)
        view._sync_source_row()
        root.update_idletasks()
        assert not view._source_row.grid_info(), "切回自制要把来源行收起来"
    finally:
        root.destroy()


@needs_display
def test_batch_page_collects_reprint(tmp_path):
    """顶部选的类型要能被 _collect_shared 读到，否则整批都投成自制。"""
    import tkinter as tk

    root = tk.Tk()
    try:
        _app, view = _tasks_view(root, tmp_path)
        view._copyright_var.set(REPRINT)
        view._source_var.set("https://example.com/origin")
        shared = view._collect_shared()
        assert shared.copyright == 2
        assert shared.source == "https://example.com/origin"
    finally:
        root.destroy()


@needs_display
def test_batch_page_rejects_reprint_without_source(tmp_path):
    """批量投稿缺来源要在开跑前拦住。

    不拦的话每个稿件都失败一次，用户看到的是一整列红色，却不知道
    其实是同一个原因——重试多少次都一样。
    """
    import tkinter as tk

    from bilibili_submit.config import TaskConfig
    from bilibili_submit.exceptions import BiliError

    root = tk.Tk()
    try:
        _app, view = _tasks_view(root, tmp_path)
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        view._tasks = [TaskConfig(name="a", type="single", file=str(video))]
        view._picked = {"0": True}
        view._source_mode = "folder"
        view._copyright_var.set(REPRINT)
        view._source_var.set("")
        with pytest.raises(BiliError, match="转载来源"):
            view._snapshot_run([0])
    finally:
        root.destroy()


@needs_display
def test_batch_page_restores_reprint_from_state(tmp_path):
    """上次选了转载并填了来源，重新打开要原样回来——包括来源行是展开的。"""
    import tkinter as tk

    from bilibili_submit.ui.state import BatchUIState

    root = tk.Tk()
    try:
        _app, view = _tasks_view(root, tmp_path)
        view._apply_state(
            BatchUIState(copyright=2, source="https://example.com/origin")
        )
        root.update_idletasks()
        assert view._copyright_var.get() == REPRINT
        assert view._source_var.get() == "https://example.com/origin"
        # 只恢复变量不恢复显隐的话，值在、框却看不见，用户会以为出处丢了
        assert view._source_row.grid_info(), "恢复转载时必须同时恢复来源行"
    finally:
        root.destroy()
