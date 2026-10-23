"""Linux / macOS 桌面适配（``ui/desktop.py``）。

这里测的三件事有个共同点：**坏了程序照样能启动**，只是界面不对——
图标空白、HiDPI 上一切小一倍、中文全是方框。这类故障没有任何测试
会发现，因为「能跑」和「能看」是两回事。
"""

from __future__ import annotations

import sys

import pytest

from bilibili_submit.ui import desktop

from conftest import needs_display


# ---------- 图标 ----------


def test_icon_candidates_are_png_outside_windows(monkeypatch):
    """非 Windows 上必须找 PNG——``iconbitmap`` 读不了 .ico。

    这是 Linux 上窗口没图标的唯一原因：代码一直在 ``iconbitmap``
    一个 .ico，而那个格式是 Windows 专属的。
    """
    monkeypatch.setattr(sys, "platform", "linux")
    candidates = desktop._icon_candidates()
    assert candidates, "至少要有候选"
    assert all(c.suffix.lower() == ".png" for c in candidates), (
        f"Linux 上应当只找 PNG，实际：{[c.name for c in candidates]}"
    )


def test_icon_candidates_are_ico_on_windows(monkeypatch):
    """Windows 上保持原样——那一侧已经验证过，别顺手改坏。"""
    monkeypatch.setattr(sys, "platform", "win32")
    assert all(c.suffix.lower() == ".ico" for c in desktop._icon_candidates())


@needs_display
def test_set_window_icon_keeps_a_reference(root, tmp_path, monkeypatch):
    """``PhotoImage`` 必须被持有，否则图标会悄悄消失。

    Tk 的 PhotoImage 被 GC 时图标就没了，而且**不报错**——表现为
    「图标闪一下就没了」，是最难排查的一类 bug。挂在 root 上是为了
    让它跟窗口同生命周期。
    """
    import tkinter as tk

    png = tmp_path / "icon.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")  # 内容不重要，下面会打桩
    monkeypatch.setattr(desktop, "_icon_candidates", lambda: [png])

    class _Photo:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(tk, "PhotoImage", _Photo)
    setattr(root, "iconphoto", lambda *a, **k: None)

    assert desktop.set_window_icon(root) is True
    assert getattr(root, "_bili_icon", None) is not None, (
        "图标要挂在窗口上——PhotoImage 被 GC 掉图标就没了，且不报错"
    )


def test_set_window_icon_survives_a_missing_file(tmp_path, monkeypatch):
    """图标找不到不能阻止启动——它是最典型的锦上添花。"""
    monkeypatch.setattr(desktop, "_icon_candidates", lambda: [tmp_path / "nope.png"])
    assert desktop.set_window_icon(object()) is False


# ---------- 缩放 ----------


class _FakeRoot:
    def __init__(self, scaling: object):
        self._scaling = scaling
        self.tk = self

    def call(self, *args):
        if args[:2] == ("tk", "scaling"):
            if isinstance(self._scaling, Exception):
                raise self._scaling
            return self._scaling
        raise AssertionError(f"未预期的调用: {args}")


def test_detect_system_scale_is_one_on_a_96dpi_screen():
    """普通屏必须算出 1.0。

    Xvfb 这类虚拟屏幕会给出 1.04 这种零头（实测 1.389/1.333）。
    真按 1.04 放大，界面会「有点不对劲又说不上哪不对」——所以按
    1/4 档对齐，1.04 归到 1.0。
    """
    assert desktop.detect_system_scale(_FakeRoot(1.389)) == 1.0
    assert desktop.detect_system_scale(_FakeRoot(4.0 / 3.0)) == 1.0


def test_detect_system_scale_picks_up_hidpi():
    """200% 缩放下要算出 2.0，否则界面整体小一倍。"""
    assert desktop.detect_system_scale(_FakeRoot(2.0 * 4.0 / 3.0)) == 2.0
    assert desktop.detect_system_scale(_FakeRoot(1.25 * 4.0 / 3.0)) == 1.25


def test_detect_system_scale_falls_back_to_one():
    """探测失败时回落 1.0——宁可不放大，也不要因为探测炸了起不来。"""
    assert desktop.detect_system_scale(_FakeRoot(RuntimeError("boom"))) == 1.0
    assert desktop.detect_system_scale(_FakeRoot(0)) == 1.0
    assert desktop.detect_system_scale(_FakeRoot(-1)) == 1.0


def test_detect_system_scale_is_clamped():
    """异常大的值要钳住——3.0 以上界面就没法用了。"""
    assert desktop.detect_system_scale(_FakeRoot(100.0)) == 3.0


# ---------- 字体 ----------


def test_resolve_families_falls_through_the_chain(monkeypatch):
    """首选字体没有时要往下找——否则中文全是方框。

    ``Noto Sans CJK SC`` 不是每个发行版都装。缺了 Tk 会回落到某种
    默认字体，中文渲染成方框，而程序不会报任何错。
    """
    import tkinter.font as tkfont

    monkeypatch.setattr(tkfont, "families", lambda root=None: ["Source Han Sans SC"])
    assert desktop.resolve_families(object())[0] == "Source Han Sans SC"


def test_resolve_families_keeps_the_first_choice_when_present(monkeypatch):
    import tkinter.font as tkfont

    monkeypatch.setattr(
        tkfont, "families", lambda root=None: ["Noto Sans CJK SC", "Source Han Sans SC"]
    )
    assert desktop.resolve_families(object())[0] == "Noto Sans CJK SC"


def test_resolve_families_gives_up_gracefully(monkeypatch):
    """一个候选都没有时返回首选，让 Tk 自己回落，不抛异常。"""
    import tkinter.font as tkfont

    monkeypatch.setattr(tkfont, "families", lambda root=None: [])
    assert desktop.resolve_families(object())[0] == desktop.CJK_FAMILIES[0]


def test_resolve_families_without_a_root_skips_the_probe():
    """没给 root 就不探测——那时建不了 Tk 查询，直接返回首选。"""
    assert desktop.resolve_families(None)[0] == desktop.CJK_FAMILIES[0]
    assert desktop.resolve_families(None)[1] == desktop.MONO_FAMILIES[0]


@needs_display
def test_resolve_families_finds_a_real_font(root):
    """真机上要能挑出系统里存在的一个中文字体。"""
    family, mono = desktop.resolve_families(root)
    from tkinter import font as tkfont

    available = set(tkfont.families(root))
    if desktop.CJK_FAMILIES[0] in available:
        assert family == desktop.CJK_FAMILIES[0]
    assert family in desktop.CJK_FAMILIES


# ---------- 系统主题 ----------


def test_system_prefers_light_without_gsettings(monkeypatch):
    """没装 gsettings（非 GNOME）时按浅色——猜错的代价最小。"""
    import subprocess

    def boom(*args, **kwargs):
        raise FileNotFoundError("gsettings")

    monkeypatch.setattr(subprocess, "run", boom)
    assert desktop.system_prefers_light() is True


def test_system_prefers_dark_is_detected(monkeypatch):
    import subprocess

    class _Proc:
        returncode = 0
        stdout = "'prefer-dark'\n"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    assert desktop.system_prefers_light() is False


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 走 win_effects")
def test_desktop_module_imports_without_a_display():
    """适配模块本身不能要求有屏——它在 launch 里是最早被 import 的之一。"""
    assert desktop.IS_LINUX == sys.platform.startswith("linux")
