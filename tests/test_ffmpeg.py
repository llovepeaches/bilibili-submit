"""ffmpeg 定位器测试。"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit import ffmpeg as ff  # noqa: E402
from bilibili_submit.exceptions import BiliError  # noqa: E402


def test_app_dir_is_project_root_when_unfrozen():
    """源码运行时 app_dir 应是项目根目录。"""
    assert ff.app_dir().name
    assert (ff.app_dir() / "bilibili_submit").is_dir()


def test_app_dir_uses_executable_when_frozen(monkeypatch, tmp_path):
    """打包后应定位到 exe 所在目录，而不是解包临时目录。"""
    fake_exe = tmp_path / "bilibili-submit.exe"
    fake_exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    assert ff.app_dir() == tmp_path.resolve()


def _make_ffmpeg(directory: Path, name: str = "ffmpeg") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_bundled_takes_priority(tmp_path, monkeypatch):
    """外置文件优先级最高，其次 imageio，最后 PATH。"""
    _make_ffmpeg(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))

    monkeypatch.setattr(ff, "_from_imageio", lambda: "/fake/imageio/ffmpeg")
    info = ff.find_ffmpeg()
    assert info is not None
    assert info.source == "bundled"
    assert Path(info.path) == tmp_path / "ffmpeg"


def test_imageio_used_when_no_bundled(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
    fake = _make_ffmpeg(tmp_path / "img")
    monkeypatch.setattr(ff, "_from_imageio", lambda: str(fake))
    monkeypatch.setattr(ff.shutil, "which", lambda _n: None)

    info = ff.find_ffmpeg()
    assert info is not None
    assert info.source == "imageio"


def test_system_path_is_last_resort(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
    monkeypatch.setattr(ff, "_from_imageio", lambda: None)
    monkeypatch.setattr(ff.shutil, "which", lambda _n: "/usr/bin/ffmpeg")

    info = ff.find_ffmpeg()
    assert info is not None
    assert info.source == "system"
    assert info.path == "/usr/bin/ffmpeg"


def test_subdirectory_layout_supported(tmp_path, monkeypatch):
    """支持 ffmpeg/ffmpeg.exe 这样的子目录布局。"""
    sub = tmp_path / "ffmpeg"
    sub.mkdir()
    _make_ffmpeg(sub)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
    monkeypatch.setattr(ff, "_from_imageio", lambda: None)
    monkeypatch.setattr(ff.shutil, "which", lambda _n: None)

    info = ff.find_ffmpeg()
    assert info is not None
    assert info.source == "bundled"


def test_missing_returns_none_when_not_required(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
    monkeypatch.setattr(ff, "_from_imageio", lambda: None)
    monkeypatch.setattr(ff.shutil, "which", lambda _n: None)
    assert ff.find_ffmpeg() is None
    assert ff.ffmpeg_status() is None


def test_missing_raises_with_actionable_hint(tmp_path, monkeypatch):
    """找不到时必须给出可操作的修复建议，而不是干巴巴一句"未找到"。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.exe"))
    monkeypatch.setattr(ff, "_from_imageio", lambda: None)
    monkeypatch.setattr(ff.shutil, "which", lambda _n: None)

    with pytest.raises(BiliError) as exc:
        ff.find_ffmpeg(required=True)
    hint = exc.value.hint
    assert "imageio-ffmpeg" in hint
    assert "PATH" in hint


def test_describe_is_human_readable(tmp_path):
    info = ff.FfmpegInfo("/usr/bin/ffmpeg", "system")
    assert "系统 PATH" in info.describe()


# ---------------------------------------------------------------------------
# 内嵌 ffmpeg（BUNDLE_FFMPEG=1 打包）
# ---------------------------------------------------------------------------

def test_meipass_dir_is_none_when_unfrozen(monkeypatch):
    """源码运行时没有 _MEIPASS，内嵌这一级必须干净地退化掉。"""
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    assert ff._meipass_dir() is None


def test_embedded_ffmpeg_is_found(tmp_path, monkeypatch):
    """打包内嵌的 ffmpeg 应能被定位到，source 标为 embedded。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    meipass = tmp_path / "_MEI123"
    _make_ffmpeg(meipass)

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "app.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(ff, "_from_imageio", lambda: None)
    monkeypatch.setattr(ff.shutil, "which", lambda _n: None)

    info = ff.find_ffmpeg()
    assert info is not None
    assert info.source == "embedded"
    assert Path(info.path) == meipass / "ffmpeg"


def test_bundled_beats_embedded(tmp_path, monkeypatch):
    """用户放在 exe 旁边的 ffmpeg 优先于内嵌的那份——方便自行换版本。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    meipass = tmp_path / "_MEI123"
    _make_ffmpeg(meipass)          # 内嵌
    _make_ffmpeg(exe_dir)          # 外置

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "app.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(ff, "_from_imageio", lambda: None)

    info = ff.find_ffmpeg()
    assert info is not None
    assert info.source == "bundled"
    assert Path(info.path) == exe_dir / "ffmpeg"


def test_embedded_beats_imageio(tmp_path, monkeypatch):
    """内嵌优先于 imageio，避免明明自带了却去用 pip 装的。"""
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    meipass = tmp_path / "_MEI123"
    _make_ffmpeg(meipass)
    fake_imageio = _make_ffmpeg(tmp_path / "img")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "app.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
    monkeypatch.setattr(ff, "_from_imageio", lambda: str(fake_imageio))

    info = ff.find_ffmpeg()
    assert info is not None
    assert info.source == "embedded"


def test_embedded_describe_is_human_readable():
    assert "exe 内嵌" in ff.FfmpegInfo("/tmp/ffmpeg", "embedded").describe()
