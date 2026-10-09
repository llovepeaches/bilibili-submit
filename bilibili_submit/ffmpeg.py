"""ffmpeg 定位。

封面抽帧需要一个 ffmpeg 可执行文件。定位顺序（先命中先用）：

1. **外置**——与 exe 同目录的 ``ffmpeg.exe``（或 ``ffmpeg/ffmpeg.exe``）。
   用户自己放的版本优先于程序自带，方便换版本。
2. **内嵌**——打进 exe 归档里的 ffmpeg（``sys._MEIPASS/ffmpeg.exe``）。
   设 ``BUNDLE_FFMPEG=1`` 打包时才会带上，见 ``bili_submit.spec``。
   内嵌让单个 exe 开箱即用，代价是体积和启动解压耗时变大。
3. **imageio-ffmpeg** 自带的二进制（``pip install imageio-ffmpeg``）。
4. **系统 PATH** 里的 ffmpeg。

找不到时抛出带明确指引的异常，而不是静默跳过。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .exceptions import BiliError

logger = logging.getLogger(__name__)

__all__ = [
    "FfmpegInfo",
    "find_ffmpeg",
    "app_dir",
    "ffmpeg_status",
    "video_meta",
]

_EXE_NAMES = ("ffmpeg.exe", "ffmpeg", "ffmpeg-win.exe", "avconv.exe")


def app_dir() -> Path:
    """程序所在目录。

    打包后是 exe 所在目录（外置 ffmpeg 就放这里），
    源码运行时是项目根目录。
    """
    if getattr(sys, "frozen", False):  # PyInstaller / PyOxidizer
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _meipass_dir() -> Path | None:
    """PyInstaller onefile 的解压目录（``sys._MEIPASS``）。

    只有打包运行（且带内嵌资源）时才存在，源码运行时为 None。
    内嵌的 ffmpeg 就躺在这个目录的根下。
    """
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else None


@dataclass
class FfmpegInfo:
    """一次定位结果。"""

    path: str
    source: str  # bundled / embedded / imageio / system

    def exists(self) -> bool:
        return bool(self.path) and Path(self.path).exists()

    def describe(self) -> str:
        labels = {
            "bundled": "程序同目录",
            "embedded": "exe 内嵌",
            "imageio": "imageio-ffmpeg",
            "system": "系统 PATH",
        }
        return f"{labels.get(self.source, self.source)}: {self.path}"


def _candidates_in_app_dir() -> list[Path]:
    base = app_dir()
    out: list[Path] = []
    for name in _EXE_NAMES:
        out.append(base / name)
        # 支持 ffmpeg/ffmpeg.exe 这样的子目录布局
        out.append(base / "ffmpeg" / name)
    return out


def _candidates_embedded() -> list[Path]:
    """打包内嵌的 ffmpeg 候选路径。"""
    base = _meipass_dir()
    if base is None:
        return []
    out: list[Path] = []
    for name in _EXE_NAMES:
        out.append(base / name)
        out.append(base / "ffmpeg" / name)
    return out


def _from_imageio() -> str | None:
    try:
        import imageio_ffmpeg  # type: ignore[import-untyped]

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # noqa: BLE001 - 未安装属正常情况
        logger.debug("imageio-ffmpeg 不可用: %s", exc)
        return None


def find_ffmpeg(required: bool = False) -> FfmpegInfo | None:
    """定位可用的 ffmpeg。

    Args:
        required: 为 True 时找不到直接抛 BiliError（附带安装指引），
            否则返回 None。
    """
    # ① 用户放在程序旁边的优先——想换 ffmpeg 版本时不必重新打包
    for candidate in _candidates_in_app_dir():
        if candidate.is_file():
            return FfmpegInfo(str(candidate), "bundled")

    # ② 打进 exe 归档里的那份
    for candidate in _candidates_embedded():
        if candidate.is_file():
            return FfmpegInfo(str(candidate), "embedded")

    from_imageio = _from_imageio()
    if from_imageio and Path(from_imageio).exists():
        return FfmpegInfo(str(from_imageio), "imageio")

    on_path = shutil.which("ffmpeg")
    if on_path:
        return FfmpegInfo(on_path, "system")

    if required:
        raise BiliError(
            "未找到 ffmpeg，无法自动生成封面",
            hint=(
                "三选一：① 把 ffmpeg.exe 放到程序同目录；"
                "② pip install imageio-ffmpeg；"
                "③ 自行安装 ffmpeg 并加入 PATH。"
                "或在配置里用 cover 指定现成的图片，跳过自动抽帧"
            ),
        )
    return None


def ffmpeg_status() -> FfmpegInfo | None:
    """给 check 命令用：探测 ffmpeg 是否就绪，不抛异常。"""
    try:
        return find_ffmpeg(required=False)
    except Exception:  # noqa: BLE001
        return None


def ffmpeg_version(info: FfmpegInfo | None = None) -> str:
    """读取 ffmpeg 版本，用于诊断输出。"""
    import subprocess

    info = info or ffmpeg_status()
    if info is None:
        return "未找到"
    try:
        proc = subprocess.run(
            [info.path, "-version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        first = (proc.stdout or proc.stderr or "").splitlines()
        return first[0] if first else "未知"
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取 ffmpeg 版本失败: %s", exc)
        return "未知"


def video_meta(path: str | Path) -> dict[str, str]:
    """读视频文件的基础元信息，给投稿页的五格元信息条用。

    走 ``ffmpeg -i``：它不产出文件、只把流信息打到 stderr 后立即退出
    （退出码 1 是正常路径，不是错误）。之所以不用 ffprobe，是因为打包
    只带 ffmpeg 一个二进制——为了五个数字再塞一个 exe 不划算。

    返回的键是五格要显示的：``resolution`` / ``duration`` / ``size`` /
    ``codec`` / ``bitrate``。拿不到的键给 ``"—"``：格子空着看起来像
    没加载完，占位符反而说明「这里本来有数」。

    Raises:
        BiliError: 文件不存在时。调用方（界面）应在此之前已拦住，
            这里再拦一道是给 CLI/测试留个明确出口。
    """
    file = Path(path).expanduser()
    if not file.is_file():
        raise BiliError(f"视频文件不存在: {file}")

    meta = {key: "—" for key in ("resolution", "duration", "codec", "bitrate")}
    try:
        meta["size"] = _human_size(file.stat().st_size)
    except OSError:
        meta["size"] = "—"

    info = ffmpeg_status()
    if info is None:
        return meta
    try:
        proc = subprocess.run(
            [info.path, "-i", str(file)],
            capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
        # ffmpeg 的元信息在 stderr（它没有输出文件可写）
        text = proc.stderr or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("读取视频信息失败: %s", exc)
        return meta

    # Duration: 00:12:34.56, start: ..., bitrate: 5200 kb/s
    _parse_ffmpeg_streams(text, meta)
    return meta


def _parse_ffmpeg_streams(text: str, meta: dict[str, str]) -> None:
    """从 ``ffmpeg -i`` 的 stderr 里抠出时长 / 码率 / 分辨率 / 编码。

    抽成纯函数是为了单测可以直接喂样例输出，不用真跑子进程。
    输出格式 ffmpeg 多年未变，但一旦上游改格式，改这一个函数就够了。
    """
    if m := re.search(r"Duration:\s*(\d+):(\d+):(\d+)", text):
        h, mnt, sec = m.group(1), m.group(2), m.group(3)
        meta["duration"] = f"{h}:{mnt}:{sec}" if h != "00" else f"{mnt}:{sec}"
    if m := re.search(r"bitrate:\s*(\d+)\s*kb/s", text):
        meta["bitrate"] = f"{m.group(1)} kbps"
    # 分辨率出现在「Video: 编码, 像素格式, 1920x1080」里——编码后面
    # 可能还挂着 (High) 这类 profile，所以用非贪婪跳到第一个 WxH
    if m := re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", text):
        meta["resolution"] = f"{m.group(1)}×{m.group(2)}"
    if m := re.search(r"Video:\s*([a-zA-Z0-9]+)", text):
        codec = m.group(1)
        meta["codec"] = {"h264": "H.264", "hevc": "H.265", "av1": "AV1"}.get(
            codec.lower(), codec.upper()
        )


def _human_size(num: int) -> str:
    """字节数 → 人读的大小，一位小数就够。"""
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"
