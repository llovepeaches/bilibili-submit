"""非 Windows 桌面环境的适配：图标、缩放、字体、系统主题。

和 :mod:`.win_effects` 是对称的两半：那个模块只管 Windows 的系统级
效果（深色标题栏、圆角），这里管 Linux / macOS 上那些**不处理就真的
不对**的东西。

三件事都必须处理，否则 Linux 上的界面是「能跑但一眼看上去没做完」：

1. **窗口图标**。``iconbitmap`` 只认 ``.ico``，是 Windows 专属格式——
   Linux 上调它要么抛 ``TclError`` 要么静默无效，于是窗口和任务栏里
   一片空白。这两个平台要走 ``iconphoto``，它只认 PNG。
2. **HiDPI**。Tk 会按屏幕 DPI 自动设 ``tk scaling``，而 :mod:`.theme`
   里的像素常量乘的是 ``UI_SCALE``。Windows 上我们主动把 scaling 钉回
   4/3（见 ``app.launch``），非 Windows 上则反过来：**读**出系统缩放再
   喂给 ``UI_SCALE``，否则字体被系统放大一倍而间距不变，文字直接溢出
   固定内边距——正是 Windows 上当年修掉的那个症状。
3. **中文字体**。``Noto Sans CJK SC`` 不是每个发行版都装。缺了的话
   Tk 会回落到某种默认字体，**中文全部渲染成方框**，整个界面没法用。
   所以按候选链挑一个系统里真有的。

这一层同样是**尽力而为**：探测失败一律回落到最保守的值，绝不让界面
因此打不开。
"""

from __future__ import annotations

import sys
from pathlib import Path

if sys.platform == "win32":  # pragma: no cover - Windows 走 win_effects
    IS_LINUX = False
else:
    IS_LINUX = sys.platform.startswith("linux")
IS_MAC = sys.platform == "darwin"

#: 各平台的中文字体候选链，按优先级排。
#: 挑**系统里真有的第一个**；都没有就返回首个候选，让 Tk 自己回落。
CJK_FAMILIES = (
    "Noto Sans CJK SC",    # Debian / Ubuntu（fonts-noto-cjk）
    "Source Han Sans SC",  # Arch / Fedora
    "Noto Sans SC",        # Google 的新命名，部分发行版
    "WenQuanYi Zen Hei",   # 老一点的国产发行版
    "Microsoft YaHei",     # Wine / 装了 Windows 字体的环境
)

#: 等宽字体候选链。等宽只用于日志与路径，缺了不至于看不懂，
#: 但混进比例字体会让列对不齐。
MONO_FAMILIES = (
    "DejaVu Sans Mono",
    "Noto Sans Mono CJK SC",
    "Liberation Mono",
    "Menlo",  # macOS
)

#: Tk 在 96 DPI 下的 scaling 值。``app.launch`` 会把它钉回去，
#: 所以探测系统缩放要**在钉之前**读。
BASE_SCALING = 4.0 / 3.0

#: 探测结果按 1/4 档对齐。
#: 一则与设置页的手动档（1.0 / 1.25 / 1.5）对得上，二则 Xvfb 这类
#: 虚拟屏幕会算出 1.04 这种零头——真按 1.04 放大只会让界面看起来
#: 「有点不对劲又说不上哪不对」。
_SNAP = 0.25

__all__ = [
    "CJK_FAMILIES",
    "IS_LINUX",
    "detect_system_scale",
    "resolve_families",
    "set_window_icon",
    "system_prefers_light",
]


# ---------- 图标 ----------


def _icon_candidates() -> list[Path]:
    """按平台给出图标文件的查找顺序。"""
    from ..ffmpeg import app_dir

    name = "bilibili-submit"
    assets = Path(__file__).resolve().parent.parent.parent / "assets"
    ico = assets / f"{name}.ico"
    png = assets / f"{name}.png"
    if sys.platform == "win32":
        return [app_dir() / f"{name}.ico", ico]
    # Linux / macOS：iconbitmap 读不了 .ico，只能走 PNG
    return [app_dir() / f"{name}.png", png]


def set_window_icon(root) -> bool:
    """给窗口设图标，找不到/设不上就跳过。

    图标缺失不该阻止启动——它是最典型的「锦上添花」，
    为一个图标让程序打不开是最糟的取舍。

    Returns:
        是否设上了。测试用它断言「Linux 上走的是 PNG 那条路」。
    """
    import tkinter as tk

    for candidate in _icon_candidates():
        if not candidate.is_file():
            continue
        if candidate.suffix.lower() == ".png":
            try:
                photo = tk.PhotoImage(file=str(candidate))
            except tk.TclError:
                continue
            # **必须持有引用**：PhotoImage 被 GC 掉时图标就没了，
            # 而且不报错——表现为「图标闪一下就没了」，极难排查。
            root._bili_icon = photo  # type: ignore[attr-defined]
            try:
                root.iconphoto(True, photo)
            except tk.TclError:
                continue
            return True
        try:
            root.iconbitmap(str(candidate))
            return True
        except tk.TclError:
            continue
    return False


# ---------- 缩放 ----------


def detect_system_scale(root) -> float:
    """从 Tk 的自动 scaling 反推系统缩放，失败回落 1.0。

    必须在 ``app.launch`` 把 scaling 钉回 4/3 **之前**调用——
    钉完之后读到的就永远是 4/3 了。

    Returns:
        >= 1.0 的缩放因子，按 1/4 档对齐。
    """
    try:
        current = float(root.tk.call("tk", "scaling"))
    except Exception:  # noqa: BLE001 - 拿不到就当普通屏
        return 1.0
    if current <= 0:
        return 1.0
    raw = current / BASE_SCALING
    snapped = round(raw / _SNAP) * _SNAP
    return max(1.0, min(3.0, snapped))


# ---------- 字体 ----------


def resolve_families(root=None) -> tuple[str, str]:
    """挑一对系统里真有的中文字体与等宽字体。

    Args:
        root: 任意 Tk 控件（用来问 ``tkinter.font.families()``）。
            传 None 时不做探测，直接返回候选链首选。

    Returns:
        ``(正文字体, 等宽字体)``。
    """
    available: set[str] | None = None
    if root is not None:
        try:
            from tkinter import font as tkfont

            available = set(tkfont.families(root))
        except Exception:  # noqa: BLE001 - 探不到就按首选走
            available = None

    def first_of(chain: tuple[str, ...]) -> str:
        if available is None:
            return chain[0]
        for name in chain:
            if name in available:
                return name
        return chain[0]

    return first_of(CJK_FAMILIES), first_of(MONO_FAMILIES)


# ---------- 系统主题 ----------


def system_prefers_light() -> bool:
    """Linux 上问 GNOME 的配色偏好；问不到就按浅色。

    只查 GNOME（``gsettings``）：它是桌面 Linux 上占比最大的一档，
    而把 KDE / Xfce / sway 各自的配置位置都写进来，收益远小于维护成本。
    问不到时返回 True 而不是猜——浅色是默认态，猜错的代价更小。

    .. note::
       调用会起子进程，所以**只在启动时调一次**，别放在重绘路径上。
       超时设了 2 秒：``gsettings`` 在某些坏掉的 dbus 环境下会一直挂着，
       而这里挂住等于界面起不来。
    """
    if not IS_LINUX:
        return True
    import subprocess

    try:
        proc = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True, text=True, timeout=2,
        )
    except Exception:  # noqa: BLE001 - 没装 gsettings / 超时 / 其它
        return True
    if proc.returncode != 0:
        return True
    return "dark" not in proc.stdout.lower()
