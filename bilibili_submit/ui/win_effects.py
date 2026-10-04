"""Windows 11 系统级视觉效果。

这一层是**尽力而为**的：能用 Win32 API 做到的就做，做不到（或不在
Windows 上）就安静地跳过，绝不让界面因此打不开。

能做到的：

- **深色标题栏**：``DWMWA_USE_IMMERSIVE_DARK_MODE``。窗口客户区由 tk
  自己画，但标题栏是系统的，不给它设这个属性，深色主题下会出现
  「深灰界面 + 亮白标题栏」的割裂感——这是最影响观感的一处。
- **窗口圆角**：``DWMWA_WINDOW_CORNER_PREFERENCE``。
- **系统主题检测**：读注册表 ``AppsUseLightTheme``，跟随系统。

做不到但要说清楚的：

- **Mica / Acrylic**：能让系统给窗口背面铺材质，但 tk 的控件背景是
  **不透明**的，材质会被控件整块盖住，实际看不见。
  所以这里只把 API 调用留着（未来若换成半透明控件即可生效），
  并在 :func:`apply_window_effects` 的文档里写明这一点，不假装实现了。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import tkinter as tk

__all__ = [
    "system_prefers_light",
    "apply_window_effects",
    "IS_WINDOWS",
]

IS_WINDOWS = sys.platform == "win32"

# DwmSetWindowAttribute 的属性号
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38

# DWM_WINDOW_CORNER_PREFERENCE
_DWMWCP_ROUND = 2
# DWM_SYSTEMBACKDROP_TYPE
_DWMSBT_MAINWINDOW = 2  # Mica


def _set_dwm_attribute(hwnd: int, attribute: int, value: int) -> bool:
    """调一次 ``DwmSetWindowAttribute``。任何失败都返回 False。"""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        result = ctypes.c_int(value)
        ok = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(attribute),
            ctypes.byref(result),
            ctypes.sizeof(result),
        )
        return ok == 0
    except Exception:  # noqa: BLE001 - 系统 API 调用失败一律当作不支持
        return False


def system_prefers_light() -> bool:
    """系统是否用浅色主题。读不到（非 Windows / 无注册表）时按浅色。

    Windows 存在 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\
    Themes\\Personalize\\AppsUseLightTheme``（0=深色 1=浅色）。
    """
    if not IS_WINDOWS:
        return True
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _type = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return bool(value)
    except Exception:  # noqa: BLE001
        return True


def apply_window_effects(root: "tk.Tk", dark: bool) -> None:
    """给窗口套上系统效果。

    Args:
        root: 顶层窗口。
        dark: 当前是否深色主题（决定标题栏要不要变深）。

    失败静默：这些效果都是锦上添花，不能因为 API 变了就让程序起不来。
    """
    if not IS_WINDOWS:
        return
    try:
        # Tk 在 Windows 上 winfo_id() 返回的就是 HWND
        hwnd = int(root.winfo_id())
    except Exception:  # noqa: BLE001
        return

    # 标题栏跟随主题。这是深色模式下最明显的一处割裂点
    _set_dwm_attribute(
        hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0
    )
    # 窗口圆角：Fluent 的窗口四角是圆的
    _set_dwm_attribute(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)

    # Mica。系统会尝试铺材质，但 tk 控件背景不透明，铺了也被盖住——
    # 留着调用是为了以后换半透明控件时能直接生效，不是装饰。
    _set_dwm_attribute(
        hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_MAINWINDOW
    )
