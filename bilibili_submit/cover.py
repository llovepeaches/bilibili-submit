"""封面处理与上传。

封面通过 ``POST /x/vu/web/cover/up`` 以 base64 表单上传，返回图片 URL，
该 URL 再作为投稿 payload 的 ``cover`` 字段。
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import subprocess
import tempfile
from pathlib import Path

from .client import BiliClient
from .exceptions import BiliError

logger = logging.getLogger(__name__)

COVER_UP_URL = "https://member.bilibili.com/x/vu/web/cover/up"

#: B 站封面大小上限
MAX_COVER_BYTES = 10 * 1024 * 1024

__all__ = ["upload_cover", "extract_first_frame", "resolve_cover"]


def upload_cover(client: BiliClient, image: str | Path) -> str:
    """上传封面图，返回可写入投稿 payload 的图片 URL。"""
    image = Path(image)
    if not image.is_file():
        raise BiliError(f"封面文件不存在: {image}")
    if image.stat().st_size > MAX_COVER_BYTES:
        raise BiliError(f"封面超过 {MAX_COVER_BYTES // 1024 // 1024}MB 上限: {image}")

    mime = mimetypes.guess_type(image.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    data_uri = f"data:{mime};base64,{encoded}"

    payload = client.post(
        COVER_UP_URL,
        data={"cover": data_uri, "csrf": client.csrf},
        timeout=(10.0, 60.0),
    )
    if not isinstance(payload, dict):
        raise BiliError("封面上传返回格式异常", raw=payload)

    # 不同版本字段名不一致，逐个兼容
    url = (
        payload.get("url")
        or payload.get("cover")
        or payload.get("cover_url")
        or ""
    )
    if not url:
        raise BiliError("封面上传未返回图片地址", raw=payload)
    return str(url)


def extract_first_frame(
    video: str | Path, output: str | Path | None = None, second: float = 1.0
) -> Path:
    """从视频抽一帧当封面。

    ffmpeg 按"外置 → imageio-ffmpeg → 系统 PATH"的顺序定位，
    都找不到时抛错并给出三条可选的解决方式。
    """
    from .ffmpeg import find_ffmpeg

    ffmpeg = find_ffmpeg(required=True)
    assert ffmpeg is not None  # required=True 保证非空

    video = Path(video)
    if output is None:
        handle = tempfile.NamedTemporaryFile(
            suffix=".jpg", prefix="bili_cover_", delete=False
        )
        handle.close()
        output = Path(handle.name)
    output = Path(output)

    cmd = [
        ffmpeg.path, "-y", "-loglevel", "error",
        "-ss", str(second), "-i", str(video),
        "-frames:v", "1", "-q:v", "2", str(output),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BiliError(f"抽取封面帧失败: {exc}") from exc

    if not output.is_file() or output.stat().st_size == 0:
        raise BiliError("ffmpeg 未生成封面文件")
    return output


def resolve_cover(
    client: BiliClient,
    spec: str | None,
    video: str | Path | None = None,
) -> str | None:
    """把配置里的 cover 字段解析成封面 URL。

    Args:
        spec: 图片路径 / ``auto``（抽首帧）/ 以 http 开头的现成 URL / None。
    """
    if not spec:
        return None
    spec = str(spec).strip()

    if spec.lower() == "auto":
        if video is None:
            raise BiliError("cover=auto 需要提供视频路径")
        frame = extract_first_frame(video)
        try:
            return upload_cover(client, frame)
        finally:
            frame.unlink(missing_ok=True)

    if spec.startswith(("http://", "https://", "//")):
        return spec

    return upload_cover(client, spec)
