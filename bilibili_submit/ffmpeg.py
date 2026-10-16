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
    "install_imageio_ffmpeg",
    "video_meta",
]

_EXE_NAMES = ("ffmpeg.exe", "ffmpeg", "ffmpeg-win.exe", "avconv.exe")

#: 一键获取时 pip 的超时秒数。装 imageio-ffmpeg 本质是从 PyPI 拖一个
#: 几十 MB 的二进制，慢网络上一两分钟很常见；但也绝不能无限等——
#: 界面上必须有反馈，否则用户以为程序死了。
INSTALL_TIMEOUT = 180


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


def _pip_tail(text: str, lines: int = 3) -> str:
    """取输出末尾几行，把 pip 的真实报错贴进提示里。

    pip 失败的真正原因几乎总在最后几行（"No matching distribution"
    "Connection timed out" "Permission denied"），而完整输出动辄几百行，
    全塞进提示等于什么都没说。
    """
    kept = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def install_imageio_ffmpeg(
    *, proxy: str | None = None, timeout: int = INSTALL_TIMEOUT
) -> FfmpegInfo:
    """装 imageio-ffmpeg，把它自带的 ffmpeg 拿回来。

    给设置页的「一键获取」按钮用。会走到这里的只有**源码/pip 运行**
    的人——打包版本来就自带 ffmpeg。frozen 环境下 pip 不可用
    （打包时把它排除了），所以那里直接给出「重新下载完整版」的指引，
    而不是抛一个看不懂的 ``ModuleNotFoundError: No module named 'pip'``。

    Args:
        proxy: 传给 pip 的 ``--proxy``。用户既然在设置页配了代理，
            装包这一步也该走它——国内网络下这两步常常是同一个问题，
            只解决一半等于没解决。
        timeout: 子进程超时秒数。

    Raises:
        BiliError: 装不上时，``hint`` 里带可操作的下一步。

    .. important::
       必须**在工作线程**调用：``subprocess.run`` 会阻塞，
       在主线程跑会把界面冻住。
    """
    if getattr(sys, "frozen", False):
        raise BiliError(
            "打包版无法自行安装组件",
            hint="请重新下载自带 ffmpeg 的版本（安装版或便携版）；"
                 "轻量命令行版请改下完整版，或自己放一个 ffmpeg.exe 在程序旁边",
        )

    cmd = [sys.executable, "-m", "pip", "install", "imageio-ffmpeg"]
    if proxy:
        cmd += ["--proxy", proxy]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise BiliError(
            f"安装超时（{timeout} 秒）",
            hint="网络太慢或被拦截。可在设置页配好代理后重试，"
                 "或手动执行 pip install imageio-ffmpeg",
        ) from None
    except OSError as exc:  # 例如 pip 被卸载、或没有解释器权限
        raise BiliError(
            f"无法启动 pip：{exc}",
            hint="手动执行 pip install imageio-ffmpeg，"
                 "或自行安装 ffmpeg 并加入 PATH",
        ) from exc

    if proc.returncode != 0:
        detail = _pip_tail(f"{proc.stdout or ''}\n{proc.stderr or ''}")
        raise BiliError(
            "安装失败",
            hint=detail or "手动执行 pip install imageio-ffmpeg 看完整报错",
        )

    # 装完立刻重新定位：pip 可能把包装到了另一个前缀下，
    # 「装成功」和「程序找得到」是两件事，必须分开验一次。
    info = find_ffmpeg(required=False)
    if info is None:
        raise BiliError(
            "包装上了，但程序还没找到它",
            hint="点「重新检测」再试一次；仍不行就重启客户端",
        )
    return info


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
