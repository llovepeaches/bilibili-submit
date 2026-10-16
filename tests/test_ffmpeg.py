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


# ---------------------------------------------------------------------------
# 一键获取（pip install imageio-ffmpeg）
# ---------------------------------------------------------------------------

def _fake_pip(monkeypatch, *, returncode: int = 0, stdout: str = "",
              stderr: str = "", error: Exception | None = None):
    """替掉 subprocess.run，记下 pip 收到的是什么命令。"""
    import subprocess

    calls: list[list[str]] = []

    def run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        if error is not None:
            raise error
        return subprocess.CompletedProcess(
            cmd, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(ff.subprocess, "run", run)
    return calls


def test_install_refuses_to_run_in_a_frozen_build(monkeypatch):
    """打包版不能自己装组件——要给「重新下载完整版」的指引。

    打包时 pip 被排除了，硬跑只会得到
    ``ModuleNotFoundError: No module named 'pip'``，对用户毫无意义。
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(BiliError) as exc:
        ff.install_imageio_ffmpeg()
    assert "打包版" in str(exc.value)
    assert "重新下载" in exc.value.hint


def test_install_runs_pip_for_the_interpreter(tmp_path, monkeypatch):
    """pip 必须走 ``sys.executable -m pip``。

    直接叫 ``pip`` 会命中 PATH 上那个，可能装到另一个 Python 里去——
    装完了程序照样找不到，这是这类"一键修复"最常见的失效方式。
    """
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    calls = _fake_pip(monkeypatch)
    monkeypatch.setattr(ff, "find_ffmpeg", lambda required=False: None)
    # 装完定位不到 -> 应当报错（下一条测这个），这里只关心命令本身
    with pytest.raises(BiliError):
        ff.install_imageio_ffmpeg()

    assert calls, "一次 pip 都没跑"
    cmd = calls[0]
    assert cmd[:3] == [sys.executable, "-m", "pip"], f"pip 调用方式不对: {cmd}"
    assert "imageio-ffmpeg" in cmd


def test_install_forwards_the_proxy(monkeypatch):
    """设置页配了代理，装包这一步也得走它。

    国内网络下「下载投稿接口」和「从 PyPI 装包」常常是同一个问题；
    只解决一半，用户会看到「代理配好了但一键获取还是超时」。
    """
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    calls = _fake_pip(monkeypatch)
    monkeypatch.setattr(ff, "find_ffmpeg", lambda required=False: None)
    with pytest.raises(BiliError):
        ff.install_imageio_ffmpeg(proxy="http://127.0.0.1:7890")

    cmd = calls[0]
    assert "--proxy" in cmd, f"没有把代理传给 pip: {cmd}"
    assert cmd[cmd.index("--proxy") + 1] == "http://127.0.0.1:7890"


def test_install_reports_timeout_with_a_next_step(monkeypatch):
    """超时不能只说"超时"——要告诉用户能做什么。"""
    import subprocess

    monkeypatch.setattr(sys, "frozen", False, raising=False)
    _fake_pip(monkeypatch, error=subprocess.TimeoutExpired("pip", 180))
    with pytest.raises(BiliError) as exc:
        ff.install_imageio_ffmpeg()
    assert "超时" in str(exc.value)
    assert "代理" in exc.value.hint


def test_install_shows_pip_tail_on_failure(monkeypatch):
    """pip 非零退出时，把输出的最后几行贴进提示。

    pip 完整输出几百行，全给等于没给；真正的原因（No matching
    distribution / Permission denied）几乎总在末尾。
    """
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    _fake_pip(
        monkeypatch,
        returncode=1,
        stdout=(
            "Collecting imageio-ffmpeg\n"
            "  Downloading imageio_ffmpeg-0.4.9-py3-none-any.whl\n"
            "  Using cached metadata\n"
            "  Building wheel\n"
        ),
        stderr="ERROR: No matching distribution found for imageio-ffmpeg\n",
    )
    with pytest.raises(BiliError) as exc:
        ff.install_imageio_ffmpeg()
    assert "No matching distribution" in exc.value.hint
    # 前面几百行的下载过程不该混进来
    assert "Downloading" not in exc.value.hint


def test_install_fails_when_ffmpeg_still_missing(monkeypatch):
    """包装上了但程序找不到，是另一回事，要说清楚。

    最常见的原因是 pip 装到了别的前缀下。这种情况报"安装成功"
    最糟——用户刷新几次都没用，只能猜。
    """
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    _fake_pip(monkeypatch, returncode=0)
    monkeypatch.setattr(ff, "find_ffmpeg", lambda required=False: None)
    with pytest.raises(BiliError) as exc:
        ff.install_imageio_ffmpeg()
    assert "还没找到" in str(exc.value)
    assert "重新检测" in exc.value.hint


def test_install_returns_the_found_ffmpeg(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    _fake_pip(monkeypatch, returncode=0)
    expected = ff.FfmpegInfo("/opt/imageio/ffmpeg", "imageio")
    monkeypatch.setattr(ff, "find_ffmpeg", lambda required=False: expected)
    assert ff.install_imageio_ffmpeg() is expected
