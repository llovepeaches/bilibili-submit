# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

⚠️ PyInstaller 不是交叉编译器（官方明确说明 "it is not a cross-compiler"），
   本 spec 必须在 Windows 上执行。Windows 一键打包：

       build_windows.bat

   没有 Windows 机器时可用 GitHub Actions 云端打包，见 .github/workflows/。
"""

from pathlib import Path

import os

from PyInstaller.utils.hooks import collect_all

BASE = Path(SPECPATH)

APP_NAME = "bilibili-submit"
VERSION = "0.1.0"

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
# ffmpeg：默认**外置**，不塞进单文件归档。
# 理由：ffmpeg 静态版约 76MB，放进 onefile 意味着每次启动都要解压到临时目录
# （启动明显变慢，且临时目录里的 exe 更容易被杀软拦截）。
# 打包脚本会把 ffmpeg.exe 复制到 dist/，与主程序同目录，运行时自动发现。
# 如需完全自包含的单文件，设环境变量 BUNDLE_FFMPEG=1 再打包（体积 +76MB）。
# ---------------------------------------------------------------------------
if os.environ.get("BUNDLE_FFMPEG") == "1":
    _d, _b, _h = collect_all("imageio_ffmpeg")
    datas += _d
    binaries += _b
    hiddenimports += _h
else:
    excludes.append("imageio_ffmpeg")

a = Analysis(
    [str(BASE / "main.py")],
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
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # 开启 UPX 会显著提高杀毒软件误报率
    upx_exclude=[],
    runtime_tmpdir=None,
    # CLI 工具必须保留控制台窗口，否则看不到扫码二维码和进度条
    console=True,
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
