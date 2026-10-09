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
VERSION = "0.2.7-rc.5"

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
#
# GUI 还默认内嵌 ffmpeg（见下方 BUNDLE_FFMPEG），所以体积会比 CLI 轻量版大；
# 名字仍由调用方通过 EXE_NAME 指定，不在这里自动加后缀——
# 自动改名会让 CI 里 dist\bilibili-submit-gui.exe 的路径对不上。
# ---------------------------------------------------------------------------
GUI = os.environ.get("GUI") == "1"

# 打包形态：见下方 ONEDIR 处的完整说明。必须在这里就定义——
# 下面的 GUI 分支要拿它决定ffmpeg 是内嵌还是外置。
ONEDIR = os.environ.get("INSTALLER") == "1"

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
    # GUI 版打包 tkinter 后体积涨了约 2MB，体积不再是主要矛盾，
    # 换「双击就能用」更划算——用窗口界面的人不会自己去装 ffmpeg。
    # 安装版（ONEDIR）例外：ffmpeg 改成放 exe 同目录，见下方 ffmpeg 段。
    print(
        "[spec] GUI mode: console=False, tkinter bundled"
        + ("" if ONEDIR else ", ffmpeg embedded by default")
    )
    if ONEDIR:
        print(
            "[spec] INSTALLER mode: onedir directory, ffmpeg is NOT embedded.\n"
            "       Copy ffmpeg.exe next to the exe after building (build_windows.bat\n"
            "       and CI already do). Onefile would re-introduce the ~60MB\n"
            "       startup decompression that this mode exists to avoid."
        )

# ---------------------------------------------------------------------------
# ffmpeg：默认**外置**，不塞进单文件归档。
#
# 三种模式：
#   * 默认（外置）——ffmpeg.exe 复制到 exe 同目录。启动快、体积小，
#     但"程序"是两个文件，拷走时要一起拷。
#   * BUNDLE_FFMPEG=1（内嵌）——ffmpeg 打进归档，运行时出现在
#     sys._MEIPASS 下，单个 exe 开箱即用。代价：体积 +约 60MB，
#     且 onefile 每次启动都要把它解压到临时目录（启动变慢，
#     临时目录里的 exe 也更容易被杀软误判）。
#   * NO_FFMPEG=1 —— 强制不带（想自己出极小体积的包时用）
#
# 内嵌用 datas 而非 binaries：binaries 会走 bindepend 依赖扫描，
# 对几十 MB 的静态 ffmpeg 极其缓慢且扫不出有用的东西；
# Windows 下能否执行只看扩展名，不经 datas 的属性位，所以 datas 完全够用。
#
# GUI 版默认内嵌（见下方 GUI 分支）：用窗口界面的人多半不会自己去
# 装 ffmpeg，而 GUI 的卖点就是「双击就能用」。命令行版保持默认外置，
# 因为用命令行的人通常已经有 ffmpeg，或者知道该怎么装。
# ---------------------------------------------------------------------------
# ⚠️ 本文件里的 print/异常消息**只能用 ASCII**。
# spec 是被 PyInstaller 自己的 Python 进程 exec 的，不走本项目的
# bilibili_submit.console（那层 UTF-8 兜底只作用于打包出来的程序）。
# Windows 上该进程 stdout 是 cp1252，中文会抛 UnicodeEncodeError，
# 而且崩在打包阶段，很难一眼看出是编码问题。

NO_FFMPEG = os.environ.get("NO_FFMPEG") == "1"

# GUI 默认内嵌 ffmpeg（用窗口界面的人不会自己去装），命令行版维持「不带」。
# _FFMPEG_EXPLICIT 区分「用户明确要求」和「按默认行为」：
#   * 明确要求（BUNDLE_FFMPEG=1）却找不到 ffmpeg -> 报错。
#     静默产出一个不含 ffmpeg 的包，用户拿到手才发现 cover 不能用，
#     不如当场失败。
#   * 默认行为（GUI 隐式内嵌）却找不到 -> 只警告并降级为不内嵌。
#     否则一台没准备 ffmpeg 的机器（CI、新同事）连 GUI 包都打不出来。
_FFMPEG_EXPLICIT = os.environ.get("BUNDLE_FFMPEG") == "1"

# onedir（安装版）**从不内嵌**：ffmpeg 由构建脚本复制到 exe 同目录。
# 这不是「内嵌也行但外置更好」，而是内嵌会把这个好处直接抵消掉——
# onedir 唯一的优势就是启动时不用解压 60MB 到 %TEMP%，ffmpeg 一内嵌
# 又得每次解压回去。
#
# 但显式要求（BUNDLE_FFMPEG=1）时**不静默忽略**：CI 或某个同事可能
# 明确想要「全塞归档里」的产物（哪怕是 onedir），直接报错让他知道
# 这个组合不支持，比给出一个行为与预期不符的包好。
if ONEDIR and _FFMPEG_EXPLICIT:
    raise SystemExit(
        "[spec] INSTALLER=1 and BUNDLE_FFMPEG=1 are incompatible.\n"
        "       onedir places ffmpeg next to the exe on purpose: embedding it\n"
        "       would re-introduce the 60MB startup decompression that onedir\n"
        "       exists to avoid.\n"
        "       Unset BUNDLE_FFMPEG, or unset INSTALLER to build onefile."
    )

BUNDLE_FFMPEG = _FFMPEG_EXPLICIT or (GUI and not NO_FFMPEG and not ONEDIR)


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
        if _FFMPEG_EXPLICIT:
            raise SystemExit(
                "[spec] BUNDLE_FFMPEG=1 but ffmpeg.exe was not found.\n"
                "       Run `python tools/setup_ffmpeg.py --dest vendor` first,\n"
                "       or set FFMPEG_EXE=<path> to point at one.\n"
                "       (or unset BUNDLE_FFMPEG to build without it)"
            )
        print(
            "[spec] WARNING: GUI default wants embedded ffmpeg but none was found.\n"
            "       Falling back to a GUI build WITHOUT ffmpeg (~13MB).\n"
            "       cover:auto will be unavailable at runtime.\n"
            "       To fix: run `python tools/setup_ffmpeg.py --dest vendor`."
        )
        BUNDLE_FFMPEG = False
    else:
        # 放到归档根目录，运行时即 sys._MEIPASS/ffmpeg.exe
        datas.append((str(_ffmpeg), "."))
        print(
            f"[spec] embedding ffmpeg: {_ffmpeg.name} "
            f"({_ffmpeg.stat().st_size / 1048576:.1f} MB)"
        )

# 判断依据用 BUNDLE_FFMPEG 的**最终值**，而不是「有没有进过 if 分支」——
# GUI 降级时会把 BUNDLE_FFMPEG 改回 False，那种情况下同样要排掉
# imageio_ffmpeg：既没内嵌也没 imageio 兜底，留着它只会白白多打 30MB。
if not BUNDLE_FFMPEG:
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

# ---------------------------------------------------------------------------
# 打包形态：onefile（单文件）vs onedir（目录）
#
# GUI 的**安装版走 onedir**。这不是为了省事，而是 onedir 有两个实打实的
# 好处：
#   1. 启动不用每次把内嵌的 ffmpeg（约 62MB）解压到临时目录。onefile
#      每次启动都要解一次，装在 Program Files 上还要往 %TEMP% 里写
#      60MB，慢且更容易被杀软拦；
#   2. 目录版可以把 ffmpeg.exe 放在**exe 同目录**——代码里本来就优先
#      找那个位置（ffmpeg.py 的 _candidates_in_app_dir），所以用户能
#      自己换 ffmpeg 版本，不用重新打包。
#
# 代价是「一个文件」变成「一个目录」，所以单文件版仍然保留：
# 便携用法（拷走即用、U 盘跑）靠它，安装版靠 onedir。
#
# 由 INSTALLER=1 开启（定义见文件上方 GUI 段旁边）。CLI 始终是 onefile：
# 命令行用户要的是 `scp 过去就能跑`，目录反而碍事。
#
# ffmpeg 的处理已在上面 BUNDLE_FFMPEG 那里定好了（onedir 恒为外置）。
# ---------------------------------------------------------------------------
def _make_exe():
    """两种打包形态共用同一份 EXE 配置。

    参数逐项相同，只有 onedir 的``exclude_binaries=True`` 与空的
    binaries/datas 位置不同——那些内容交给 COLLECT。写成函数是为了
    以后调样式时只改一处，两边不会悄悄跑偏。
    """
    onedir = ONEDIR
    return EXE(
        pyz,
        a.scripts,
        # onedir：二进制与数据由 COLLECT 收进 _internal/，这里必须空着
        [] if onedir else a.binaries,
        [] if onedir else a.datas,
        exclude_binaries=onedir,
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


if ONEDIR:
    exe = _make_exe()
    # 目录名用 EXE_NAME：安装器按固定名字找这个目录，
    # 改名的话 installer.iss 和 CI 里都得跟着改，容易漏。
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name=EXE_NAME,
    )
else:
    exe = _make_exe()
