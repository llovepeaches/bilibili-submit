"""视频分片上传（upos 协议）。

一次上传的完整流程：

    1. preupload  取上传凭证（endpoint / upos_uri / auth / biz_id / chunk_size）
    2. 申请 upload_id
    3. 逐片 PUT，服务端决定分片大小，末片按实际余数
    4. 合并分片
    5. 返回 ``filename``（= upos_uri 的 stem），它才是投稿接口要的字段

两个最容易写错的地方：
    * ``videos[].filename`` 不是本地文件名，而是 ``upos_uri`` 去掉 ``upos://``
      前缀和扩展名后的主体。
    * ``chunk_size`` 必须由服务端给出，硬编码 10MiB 在部分线路上会失败。
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import BiliClient
from .exceptions import BiliError, MergeFailedError, UploadError
from .lines import PREUPLOAD_URL, UploadLine, pick_line

logger = logging.getLogger(__name__)

__all__ = [
    "UploadBucket",
    "UploadResult",
    "preupload",
    "upload_video",
    "ResumeState",
]

#: 并发上限。更高不会更快，反而容易触发 upos 限流
MAX_CONCURRENCY = 4

DEFAULT_CLIENT_VERSION = "2.4.14"
DEFAULT_BUILD = "2048000"
DEFAULT_WEB_VERSION = "2.0.0"


# ---------- preupload ----------


@dataclass
class UploadBucket:
    """preupload 返回的上传凭证。"""

    endpoint: str
    upos_uri: str
    auth: str
    biz_id: int
    chunk_size: int
    put_query: str = ""

    @property
    def path(self) -> str:
        """``upos://bfs/xxx/yyy.mp4`` → ``bfs/xxx/yyy.mp4``"""
        return self.upos_uri.split("upos://", 1)[-1]

    @property
    def base_url(self) -> str:
        endpoint = self.endpoint
        if endpoint.startswith("//"):
            endpoint = "https:" + endpoint
        elif not endpoint.startswith("http"):
            endpoint = "https://" + endpoint
        return f"{endpoint.rstrip('/')}/{self.path.lstrip('/')}"

    @property
    def filename(self) -> str:
        """投稿接口需要的 filename：upos_uri 的主体部分（无扩展名）。"""
        return Path(self.path).stem


def preupload(
    client: BiliClient,
    line: UploadLine,
    name: str,
    size: int,
    profile: str = "ugcupos/bup",
    timeout: tuple[float, float] = (10.0, 30.0),
) -> UploadBucket:
    """申请上传凭证。

    Args:
        profile: ``ugcupos/bup`` 直传（国内）；``ugcupos/bupfetch`` 适合港澳台/海外。
    """
    params: dict[str, str] = dict(urllib.parse.parse_qsl(line.query))
    params.update(
        {
            "r": "upos",
            "profile": profile,
            "ssl": "0",
            "version": DEFAULT_CLIENT_VERSION,
            "build": DEFAULT_BUILD,
            "name": name,
            "size": str(size),
            "webVersion": DEFAULT_WEB_VERSION,
        }
    )

    resp = client.request_raw(
        "GET", PREUPLOAD_URL, params=params, timeout=timeout
    )
    try:
        data = resp.json()
    except ValueError as exc:
        raise BiliError(
            "preupload 返回非 JSON，接口可能已变更", raw=resp.text[:500]
        ) from exc

    if data.get("OK") != 1:
        raise BiliError(
            f"preupload 失败: {data.get('message') or data}",
            raw=data,
        )

    missing = [
        k for k in ("endpoint", "upos_uri", "auth", "biz_id") if not data.get(k)
    ]
    if missing:
        raise BiliError(f"preupload 返回缺少字段 {missing}", raw=data)

    chunk_size = int(data.get("chunk_size") or 0)
    if chunk_size <= 0:
        chunk_size = 10 * 1024 * 1024
        logger.debug("服务端未给出 chunk_size，回退 10MiB")

    return UploadBucket(
        endpoint=str(data["endpoint"]),
        upos_uri=str(data["upos_uri"]),
        auth=str(data["auth"]),
        biz_id=int(data["biz_id"]),
        chunk_size=chunk_size,
        put_query=str(data.get("put_query", "")),
    )


def _request_upload_id(client: BiliClient, bucket: UploadBucket) -> str:
    """申请 upload_id（S3 风格分片的第一步）。"""
    url = f"{bucket.base_url}?uploads&output=json"
    resp = client.request_raw(
        "POST",
        url,
        headers={"X-Upos-Auth": bucket.auth},
        timeout=(10.0, 30.0),
    )
    try:
        data = resp.json()
    except ValueError:
        # 部分线路不需要显式申请，直接返回空表示沿用服务端默认
        logger.debug("申请 upload_id 返回非 JSON，使用空 upload_id")
        return ""
    upload_id = data.get("upload_id") or data.get("uploadId") or ""
    if not upload_id:
        logger.debug("未取得 upload_id，将以无 id 方式续传")
    return str(upload_id)


# ---------- 断点续传 ----------


@dataclass
class ResumeState:
    """单个文件的续传状态，落盘为 JSON。"""

    upload_id: str = ""
    path: str = ""
    size: int = 0
    chunks: int = 0
    done: set[int] = field(default_factory=set)
    parts: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {
            "upload_id": self.upload_id,
            "path": self.path,
            "size": self.size,
            "chunks": self.chunks,
            "done": sorted(self.done),
            "parts": self.parts,
            "created_at": self.created_at,
        }

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "ResumeState":
        return cls(
            upload_id=str(payload.get("upload_id", "")),
            path=str(payload.get("path", "")),
            size=int(payload.get("size", 0)),
            chunks=int(payload.get("chunks", 0)),
            done=set(payload.get("done", [])),
            parts=list(payload.get("parts", [])),
            created_at=float(payload.get("created_at", time.time())),
        )


def _state_path(video: Path, state_dir: Path) -> Path:
    import hashlib

    stat = video.stat()
    digest = hashlib.sha1(
        f"{video.resolve()}|{stat.st_size}|{int(stat.st_mtime)}".encode()
    ).hexdigest()[:16]
    return state_dir / f"{digest}.json"


def _load_state(video: Path, state_dir: Path, ttl: float) -> ResumeState | None:
    path = _state_path(video, state_dir)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        state = ResumeState.from_json(payload)
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("续传状态损坏，忽略: %s", exc)
        return None
    if state.size != video.stat().st_size:
        return None
    if time.time() - state.created_at > ttl:
        logger.debug("续传状态已过期（>%.0fs），重新上传", ttl)
        path.unlink(missing_ok=True)
        return None
    return state


def _save_state(video: Path, state_dir: Path, state: ResumeState) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = _state_path(video, state_dir)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state.to_json(), ensure_ascii=False), encoding="utf-8"
    )
    os.replace(tmp, path)


def _clear_state(video: Path, state_dir: Path) -> None:
    _state_path(video, state_dir).unlink(missing_ok=True)


# ---------- 分片 ----------


def _chunks_of(size: int, chunk_size: int) -> int:
    if size <= 0:
        return 1
    return max(1, math.ceil(size / chunk_size))


def _put_chunk(
    client: BiliClient,
    bucket: UploadBucket,
    upload_id: str,
    index: int,
    total_chunks: int,
    total_size: int,
    payload: bytes,
) -> dict[str, Any]:
    """上传单个分片，返回该分片的 part 描述。"""
    start = index * bucket.chunk_size
    end = start + len(payload)
    params = {
        "uploadId": upload_id,
        "chunks": total_chunks,
        "total": total_size,
        "chunk": index,
        "size": len(payload),
        "start": start,
        "end": end,
        "partNumber": index + 1,
    }
    resp = client.request_raw(
        "PUT",
        bucket.base_url,
        params={k: str(v) for k, v in params.items()},
        data=payload,
        headers={
            "X-Upos-Auth": bucket.auth,
            "Content-Type": "application/octet-stream",
        },
        timeout=(10.0, 180.0),
    )
    if not (200 <= resp.status_code < 300):
        raise UploadError(
            f"分片 {index} 上传失败: HTTP {resp.status_code}",
            raw=resp.text[:300],
        )
    etag = "etag"
    try:
        body = resp.json()
        if isinstance(body, dict):
            etag = str(body.get("etag") or body.get("eTag") or "etag")
    except ValueError:
        pass
    return {"partNumber": index + 1, "eTag": etag}


def _merge_chunks(
    client: BiliClient,
    bucket: UploadBucket,
    upload_id: str,
    name: str,
    parts: list[dict[str, Any]],
    profile: str,
) -> None:
    """合并分片。

    upos 直传线路在最后一片完成后会自动合并、不存在该端点，因此非 JSON
    响应只告警不报错；但只要服务端明确回了 JSON 且 ``OK != 1``，就必须抛错，
    否则后面投稿会拿到一个无效的 filename。
    """
    params = {
        "name": name,
        "uploadId": upload_id,
        "biz_id": bucket.biz_id,
        "output": "json",
        "profile": profile,
    }
    resp = client.request_raw(
        "POST",
        bucket.base_url,
        params={k: str(v) for k, v in params.items()},
        data=json.dumps({"parts": parts}).encode("utf-8"),
        headers={
            "X-Upos-Auth": bucket.auth,
            "Content-Type": "application/json",
        },
        timeout=(10.0, 60.0),
    )
    try:
        data = resp.json()
    except ValueError:
        logger.debug("合并端点未返回 JSON（可能已自动合并），跳过")
        return
    if data.get("OK") != 1:
        raise MergeFailedError(f"合并分片失败: {data}", raw=data)


# ---------- 对外主入口 ----------


@dataclass
class UploadResult:
    filename: str
    size: int
    bucket: UploadBucket
    reused: bool = False


def upload_video(
    client: BiliClient,
    video: str | Path,
    line: UploadLine | None = None,
    profile: str = "ugcupos/bup",
    concurrency: int = 3,
    resume: bool = True,
    state_dir: str | Path | None = None,
    resume_ttl: float = 7200.0,
    chunk_retries: int = 3,
    show_progress: bool = True,
) -> UploadResult:
    """上传一个视频，返回投稿所需的 filename。

    Args:
        concurrency: 并发分片数，会被限制在 1..MAX_CONCURRENCY。
        resume: 是否允许断点续传。
        resume_ttl: 续传状态有效期（秒）。upos 的 upload_id 服务端 TTL 未公开，
            默认 2 小时，超时自动重传。
    """
    video = Path(video)
    if not video.is_file():
        raise UploadError(f"视频文件不存在: {video}")

    size = video.stat().st_size
    if size == 0:
        raise UploadError(f"视频文件为空: {video}")

    if line is None:
        line = pick_line(client)

    bucket = preupload(client, line, video.name, size, profile=profile)
    logger.debug("上传凭证: endpoint=%s uri=%s", bucket.endpoint, bucket.upos_uri)

    concurrency = max(1, min(int(concurrency), MAX_CONCURRENCY))
    total_chunks = _chunks_of(size, bucket.chunk_size)

    state_root = Path(state_dir) if state_dir else Path(
        os.path.expanduser("~/.config/bilibili_submit/state")
    )

    state = _load_state(video, state_root, resume_ttl) if resume else None
    if state and state.chunks != total_chunks:
        state = None

    upload_id = state.upload_id if state else ""
    if not upload_id:
        upload_id = _request_upload_id(client, bucket)
        state = ResumeState(
            upload_id=upload_id,
            path=str(video.resolve()),
            size=size,
            chunks=total_chunks,
            created_at=time.time(),
        )
    done = set(state.done) if state else set()
    parts: list[dict[str, Any]] = list(state.parts) if state else []
    reused = bool(done)

    pending = [i for i in range(total_chunks) if i not in done]
    if reused:
        logger.info(
            "断点续传：%d/%d 分片已完成，继续上传剩余 %d 片",
            len(done), total_chunks, len(pending),
        )

    progress = _make_progress(show_progress, size, video.name, len(done), bucket.chunk_size)

    def worker(index: int) -> dict[str, Any]:
        with open(video, "rb") as fh:
            fh.seek(index * bucket.chunk_size)
            payload = fh.read(bucket.chunk_size)
        last_exc: Exception | None = None
        for attempt in range(chunk_retries):
            try:
                part = _put_chunk(
                    client, bucket, upload_id, index, total_chunks, size, payload
                )
                progress(len(payload))
                return part
            except Exception as exc:  # noqa: BLE001 - 单片失败独立重试
                last_exc = exc
                if attempt + 1 < chunk_retries:
                    time.sleep(2 ** attempt)
        raise UploadError(f"分片 {index} 连续 {chunk_retries} 次失败") from last_exc

    try:
        if pending:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = {pool.submit(worker, i): i for i in pending}
                for future in as_completed(futures):
                    index = futures[future]
                    part = future.result()  # 抛出即中断整个上传
                    parts.append(part)
                    done.add(index)
                    if resume and state is not None:
                        state.done = done
                        state.parts = parts
                        _save_state(video, state_root, state)
    finally:
        close = getattr(progress, "close", None)
        if callable(close):
            close()

    # 完成顺序 ≠ 分片顺序，合并前必须排序
    parts.sort(key=lambda p: p["partNumber"])

    _merge_chunks(client, bucket, upload_id, video.name, parts, profile)

    if resume:
        _clear_state(video, state_root)

    return UploadResult(
        filename=bucket.filename, size=size, bucket=bucket, reused=reused
    )


def _make_progress(
    enabled: bool, total: int, name: str, done_bytes: int, chunk_size: int
):
    """返回进度回调；缺少 tqdm 时退化为 no-op。"""
    if not enabled:
        return lambda _n: None
    try:
        from tqdm import tqdm  # type: ignore[import-untyped]
    except ImportError:
        logger.info("上传 %s（%.1f MB）", name, total / 1024 / 1024)
        return lambda _n: None

    bar = tqdm(
        total=total,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        desc=f"上传 {name}",
        initial=min(done_bytes * chunk_size, total),
    )
    return bar.update
