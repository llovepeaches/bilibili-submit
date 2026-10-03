"""打包配置（bili_submit.spec）的行为测试。

spec 不是普通模块——它引用了 PyInstaller 注入的 ``Analysis``/``EXE`` 等
全局名，直接 import 会炸。这里只 exec 它的**前半段**（纯 Python 配置部分），
足以覆盖 ffmpeg 内嵌、EXE_NAME 这些我们自己写的逻辑。
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "bili_submit.spec"

# 只取到 `a = Analysis(` 之前：后半段是 PyInstaller 的构建指令。
_HEAD = SPEC.read_text(encoding="utf-8").split("a = Analysis(")[0]


def _exec_head(env: dict[str, str] | None = None) -> dict:
    """在给定环境变量下执行 spec 前半段，返回其命名空间。"""
    ns: dict = {"SPECPATH": str(ROOT), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        for key, value in (env or {}).items():
            mp.setenv(key, value)
        exec(compile(_HEAD, str(SPEC), "exec"), ns)
    return ns


def test_app_name_and_version_are_consistent():
    """spec 里的版本号要和包内 __version__ 一致，否则 --version 会骗人。"""
    ns = _exec_head()
    from bilibili_submit import __version__

    assert ns["VERSION"] == __version__, (
        f"spec VERSION={ns['VERSION']} 与 __version__={__version__} 不一致"
    )


def test_exe_name_defaults_to_app_name():
    ns = _exec_head()
    assert ns["EXE_NAME"] == ns["APP_NAME"]


def test_exe_name_is_overridable():
    """同一份 spec 要能产出不同名的 exe（轻量版 + 内置 ffmpeg 版）。"""
    ns = _exec_head({"EXE_NAME": "bilibili-submit-standalone"})
    assert ns["EXE_NAME"] == "bilibili-submit-standalone"


def test_ffmpeg_not_bundled_by_default():
    """默认不内嵌 ffmpeg，且 imageio 被排除以减小体积。"""
    ns = _exec_head()
    assert ns["BUNDLE_FFMPEG"] is False
    assert "imageio_ffmpeg" in ns["excludes"]
    assert not any("ffmpeg.exe" in str(d[0]) for d in ns["datas"])


def test_ffmpeg_is_embedded_when_requested(tmp_path):
    """BUNDLE_FFMPEG=1 时 ffmpeg.exe 要进 datas，落到归档根。"""
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"MZ fake")

    ns = _exec_head({"BUNDLE_FFMPEG": "1", "FFMPEG_EXE": str(fake)})
    assert ns["BUNDLE_FFMPEG"] is True

    embedded = [d for d in ns["datas"] if str(d[0]).endswith("ffmpeg.exe")]
    assert len(embedded) == 1, f"期望内嵌一份 ffmpeg，实际 {embedded}"
    # 目的目录是 "."，运行时即 sys._MEIPASS/ffmpeg.exe
    assert embedded[0][1] == "."


def test_ffmpeg_exe_env_beats_vendor(tmp_path):
    """FFMPEG_EXE 优先级最高，方便直接指定现成的二进制。

    让 vendor/ffmpeg.exe 和 FFMPEG_EXE 同时存在，验证前者不会抢先。
    """
    from_env = tmp_path / "custom.exe"
    from_env.write_bytes(b"MZ")
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "ffmpeg.exe").write_bytes(b"MZ")

    # SPECPATH 指向 tmp_path，spec 才会去那儿找 vendor/
    ns: dict = {"SPECPATH": str(tmp_path), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FFMPEG_EXE", str(from_env))
        exec(compile(_HEAD, str(SPEC), "exec"), ns)
        assert ns["_resolve_ffmpeg_exe"]() == from_env


def test_vendor_is_discovered_when_no_env(tmp_path):
    """没设 FFMPEG_EXE 时，vendor/ffmpeg.exe 是约定落点。"""
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    expected = vendor / "ffmpeg.exe"
    expected.write_bytes(b"MZ")

    ns: dict = {"SPECPATH": str(tmp_path), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("FFMPEG_EXE", raising=False)
        exec(compile(_HEAD, str(SPEC), "exec"), ns)
        assert ns["_resolve_ffmpeg_exe"]() == expected


def test_missing_ffmpeg_fails_loudly(tmp_path):
    """要内嵌却找不到 ffmpeg 时，必须硬失败并给出可操作的指引。"""
    # SPECPATH 指向空目录，确保 vendor/assets 下都没有 ffmpeg
    empty = tmp_path / "empty"
    empty.mkdir()
    ns: dict = {"SPECPATH": str(empty), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("BUNDLE_FFMPEG", "1")
        with pytest.raises(SystemExit) as exc:
            exec(compile(_HEAD, str(SPEC), "exec"), ns)
    message = str(exc.value)
    assert "setup_ffmpeg.py" in message
    assert "FFMPEG_EXE" in message


def test_gui_mode_keeps_tkinter():
    """GUI 模式必须把 tkinter 从 excludes 里放出来并补 hiddenimport。

    CLI 版为了瘦身默认排除 tkinter，直接拿它打 GUI 会得到一个
    启动即静默退出的 exe（windowed 程序看不到报错），很难排查。
    """
    ns = _exec_head({"GUI": "1"})
    assert ns["GUI"] is True
    assert "tkinter" not in ns["excludes"], "GUI 模式不能排除 tkinter"
    assert "tk" not in ns["excludes"]
    for name in ("tkinter", "tkinter.ttk", "tkinter.filedialog"):
        assert name in ns["hiddenimports"], f"{name} 未加入 hiddenimports"


def test_gui_mode_uses_gui_entry_point():
    """GUI 版入口必须是 main_gui.py，不能用 main.py。

    曾经踩过：main.py 走 argparse，子命令是必填的，windowed exe
    双击启动没有参数，直接以退出码 2 退出，界面根本起不来。
    """
    src = SPEC.read_text(encoding="utf-8")
    assert "main_gui.py" in src
    assert 'BASE / ("main_gui.py" if GUI else "main.py")' in src, (
        "Analysis 的入口必须随 GUI 开关切换"
    )


def test_gui_entry_point_exists():
    """入口文件要真的在，否则打包时才炸就太晚了。"""
    assert (ROOT / "main_gui.py").is_file()


def test_cli_mode_still_excludes_tkinter():
    """CLI 版继续排除 tkinter，别因为加了 GUI 就把体积带上去了。"""
    ns = _exec_head()
    assert ns["GUI"] is False
    assert "tkinter" in ns["excludes"]


def test_spec_survives_cp1252_stdout(tmp_path, monkeypatch):
    """spec 在 cp1252 控制台下不能被中文输出搞崩。

    这是真实踩过的坑：PyInstaller 用 cp1252 的 stdout 执行 spec，
    里面一句中文 print 就让整个打包失败，而报错指向 codecs.charmap_encode，
    很难一眼看出是编码问题。spec 顶部会把流切成 UTF-8 兜底。
    """
    import io

    class _Cp1252(io.TextIOWrapper):
        def __init__(self) -> None:
            super().__init__(
                io.BytesIO(), encoding="cp1252", errors="strict",
                write_through=True,
            )

    fake = _Cp1252()
    monkeypatch.setattr(sys, "stdout", fake)
    # 走内嵌分支，确保那条 print 真的被执行到
    source = tmp_path / "ffmpeg.exe"
    source.write_bytes(b"MZ fake")
    # 不抛 UnicodeEncodeError 即通过
    _exec_head({"BUNDLE_FFMPEG": "1", "FFMPEG_EXE": str(source)})
    # 兜底应真的把流切成了 utf-8
    assert fake.encoding == "utf-8"
