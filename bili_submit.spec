# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

⚠️ PyInstaller 不是交叉编译器（官方明确说明 "it is not a cross-compiler"），
   本 spec 必须在 Windows 上执行。Windows 一键打包：

       build_windows.bat

   没有 Windows 机器时可用 GitHub Actions 云端打包，见 .github/workflows/。
"""

from pathlib import Path

import os
import sys as _sys

from PyInstaller.utils.hooks import collect_all

# 本文件由 PyInstaller 的 Python 进程 exec，不走 bilibili_submit.console
# （那层 UTF-8 兜底只作用于打包出来的程序）。Windows 上该进程 stdout 是
# cp1252，任何中文输出都会抛 UnicodeEncodeError，且崩在打包阶段很难定位。
# 与其靠人记住"别写中文"，不如直接把流切成 UTF-8 兜底。
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(_sys, _stream_name, None)
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 切不了就维持原样，不该为此中断打包
            pass
del _stream_name, _stream

BASE = Path(SPECPATH)

APP_NAME = "bilibili-submit"
VERSION = "0.1.4"

# 同一份 spec 要产出两个 exe（轻量版 + 内置 ffmpeg 版），名字靠环境变量区分。
# 不设 EXE_NAME 时沿用 APP_NAME。
EXE_NAME = os.environ.get("EXE_NAME", "").strip() or APP_NAME

datas = [
    # 配置示例随包分发，用户解压后可直接改名使用
    (str(BASE / "config" / "config.example.yaml"), "config"),
    # 使用说明也放一份在 exe 旁边
    (str(BASE / "README.md"), "."),
]
binaries: list[str] = []
hiddenimports: list[str] = [
    "requests",
    "urllib3",
    "charset_normalizer",
    "idna",
    "yaml",
    "tqdm",
    "qrcode",
    "qrcode.image.base",
    # 软依赖：Pillow 缺失时 PIL 相关 hiddenimport 不存在，忽略即可
]

# certifi 的 CA 证书最容易在打包后丢失，导致所有 HTTPS 请求报 SSL 错误，
# 这里强制收集其全部数据文件。
for _pkg in ("certifi",):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

excludes: list[str] = [
    # 本项目用不到的大块头，去掉能显著减小体积
    "tkinter", "unittest", "pydoc", "doctest",
    "numpy", "pandas", "scipy", "matplotlib", "PIL", "PyQt5", "PySide2",
    "IPython", "notebook", "jupyter",
    "pytest", "setuptools", "pip", "wheel", "distutils",
    "sqlite3", "test", "lib2to3",
    # 跨平台/框架无关项
    "win32api", "win32com", "pythoncom", "pywin32",
    "macosx", "linux", "tk", "curses",
]

# ---------------------------------------------------------------------------
# GUI 模式：产出独立窗口程序（无控制台黑框）。
#
# 三个配套改动缺一不可：
#   1. console=False —— 否则弹窗时还跟着一个黑窗口
#   2. 把 tkinter / tk 从 excludes 里去掉 —— 上面默认排除是为了给 CLI 瘦身
#   3. 显式 hiddenimport —— tkinter 的子模块（ttk、font、messagebox 等）
#      是按需 import 的，静态分析扫不到，不写进去运行时报 ModuleNotFoundError
# ---------------------------------------------------------------------------
GUI = os.environ.get("GUI") == "1"

if GUI:
    for _name in ("tkinter", "tk"):
        if _name in excludes:
            excludes.remove(_name)
    hiddenimports += [
        "tkinter",
        "tkinter.ttk",
        "tkinter.font",
        "tkinter.messagebox",
        "tkinter.filedialog",
        "tkinter.commondialog",
    ]
    print("[spec] GUI mode: console=False, tkinter bundled")

# ---------------------------------------------------------------------------
# ffmpeg：默认**外置**，不塞进单文件归档。
#
# 两种模式：
#   * 默认（外置）——ffmpeg.exe 复制到 exe 同目录。启动快、体积小，
#     但"程序"是两个文件，拷走时要一起拷。
#   * BUNDLE_FFMPEG=1（内嵌）——ffmpeg 打进归档，运行时出现在
#     sys._MEIPASS 下，单个 exe 开箱即用。代价：体积 +约 85MB，
#     且 onefile 每次启动都要把它解压到临时目录（启动变慢，
#     临时目录里的 exe 也更容易被杀软误判）。
#
# 内嵌用 datas 而非 binaries：binaries 会走 bindepend 依赖扫描，
# 对 85MB 的静态 ffmpeg 极其缓慢且扫不出有用的东西；
# Windows 下能否执行只看扩展名，不经 datas 的属性位，所以 datas 完全够用。
# ---------------------------------------------------------------------------
# ⚠️ 本文件里的 print/异常消息**只能用 ASCII**。
# spec 是被 PyInstaller 自己的 Python 进程 exec 的，不走本项目的
# bilibili_submit.console（那层 UTF-8 兜底只作用于打包出来的程序）。
# Windows 上该进程 stdout 是 cp1252，中文会抛 UnicodeEncodeError，
# 而且崩在打包阶段，很难一眼看出是编码问题。

BUNDLE_FFMPEG = os.environ.get("BUNDLE_FFMPEG") == "1"


def _resolve_ffmpeg_exe() -> Path | None:
    """找要内嵌的 ffmpeg.exe。

    优先级：FFMPEG_EXE 环境变量 > vendor/ffmpeg.exe > assets/ffmpeg.exe > 根目录。
    vendor/ 是推荐的落点——setup_ffmpeg.py --dest vendor 就是往这放。
    """
    env = os.environ.get("FFMPEG_EXE", "").strip()
    if env:
        candidate = Path(env)
        if candidate.is_file():
            return candidate
        print(f"[spec] WARNING: FFMPEG_EXE does not exist, ignored -> {env}")

    for rel in ("vendor/ffmpeg.exe", "assets/ffmpeg.exe", "ffmpeg.exe"):
        candidate = BASE / rel
        if candidate.is_file():
            return candidate
    return None


if BUNDLE_FFMPEG:
    _ffmpeg = _resolve_ffmpeg_exe()
    if _ffmpeg is None:
        raise SystemExit(
            "[spec] BUNDLE_FFMPEG=1 but ffmpeg.exe was not found.\n"
            "       Run `python tools/setup_ffmpeg.py --dest vendor` first,\n"
            "       or set FFMPEG_EXE=<path> to point at one."
        )
    # 放到归档根目录，运行时即 sys._MEIPASS/ffmpeg.exe
    datas.append((str(_ffmpeg), "."))
    print(
        f"[spec] embedding ffmpeg: {_ffmpeg.name} "
        f"({_ffmpeg.stat().st_size / 1048576:.1f} MB)"
    )
else:
    excludes.append("imageio_ffmpeg")

a = Analysis(
    # GUI 版必须用 main_gui.py：main.py 是命令行入口，argparse 的子命令
    # 是必填的，windowed exe 双击启动没参数，会直接以退出码 2 退出。
    [str(BASE / ("main_gui.py" if GUI else "main.py"))],
    pathex=[str(BASE)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    # 便于排查"明明 import 了却没被打进去"的问题
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=EXE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # 开启 UPX 会显著提高杀毒软件误报率
    upx_exclude=[],
    runtime_tmpdir=None,
    # CLI 工具必须保留控制台窗口，否则看不到扫码二维码和进度条；
    # GUI 版反过来——弹窗时不能再跟一个黑框。
    console=not GUI,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(BASE / "assets" / f"{APP_NAME}.ico"),
    version=str(BASE / "assets" / "version_info.txt")
    if (BASE / "assets" / "version_info.txt").exists()
    else None,
)
