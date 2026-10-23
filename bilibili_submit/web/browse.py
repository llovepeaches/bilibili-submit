"""浏览服务器本地目录。

Web 版的视频来源是「服务器上的目录」而不是浏览器上传——上传一个
2GB 的视频要先把它整个搬到临时目录，而程序本来就要读本地文件，
再绕一圈纯属浪费。代价是这个接口能列出任意路径，所以下面的约束
不是可选的安全加固，是这个设计成立的前提。

三条硬约束：

1. **路径先 ``realpath`` 再判 ``..``**。用户传 ``/home/a/../../etc``
   这种，不 realpath 就看不出来。symlink 也一样：``~/link`` 指向
   ``/etc`` 的话，只看字符串人畜无害。
2. **只列子目录和视频文件**。不列任意文件——这个接口的用途就是选
   投稿目录，列出 ``id_rsa`` 没有意义且纯属增加暴露面。
3. **拒绝伪文件系统**（``/proc`` ``/sys`` ``/dev``）。这些目录里的
   "文件"大小是假的、读起来可能永不返回，把它们列出来只会让界面卡死。

.. note::
   **刻意不做路径白名单**。只监听 127.0.0.1 时，白名单挡不住任何
   真实威胁（攻击者得先能在本机发请求，那时他已经能做更多事）；
   而一旦有人把它挂到内网，白名单配错的表现是「选不了目录」，
   那是最难排查的一类问题——看日志一切正常，就是没有文件。
"""

from __future__ import annotations

import os
from pathlib import Path

from ..config import VIDEO_SUFFIXES
from ..exceptions import ConfigError

__all__ = [
    "FORBIDDEN_ROOTS",
    "MAX_LISTED_FILES",
    "browse",
    "default_root",
    "reject_path",
]

#: 不列这些子树。它们是伪文件系统，列出来既没意义又可能挂住。
FORBIDDEN_ROOTS = ("/proc", "/sys", "/dev")

#: 单次最多列多少个视频文件。
#: 一个目录两千个视频是可能的（抓起整季剧集的人很常见），全量返回
#: 既慢又让浏览器渲染卡死。截断时明确告诉前端「还有更多」，
#: 而不是悄悄少给——静默截断会被当成 bug。
MAX_LISTED_FILES = 2000


def reject_path(raw: str) -> Path:
    """把用户给的路径变成可以安全列出的 :class:`Path`。

    Raises:
        ConfigError: 路径不存在、不是目录、或在禁用子树里。
    """
    text = (raw or "").strip()
    if not text:
        raise ConfigError("路径为空")

    resolved = Path(os.path.realpath(os.path.expanduser(text)))
    if not resolved.is_dir():
        raise ConfigError(f"目录不存在: {resolved}")

    for root in FORBIDDEN_ROOTS:
        if resolved == Path(root) or resolved.is_relative_to(Path(root)):
            raise ConfigError(f"不允许浏览该目录: {resolved}")
    return resolved


def default_root() -> str:
    """打开浏览框时默认停在哪——用户主目录。"""
    home = Path.home()
    return str(home) if home.is_dir() else os.path.expanduser("~")


def browse(raw: str) -> dict:
    """列出一个目录的内容。

    Returns:
        ``{"path", "parent", "dirs", "files", "truncated"}``。
        ``files`` 每项 ``{"name", "size"}``，只含视频。

    Raises:
        ConfigError: 路径不合法（见 :func:`reject_path`）。
    """
    resolved = reject_path(raw)

    dirs: list[dict[str, object]] = []
    files: list[dict[str, object]] = []
    truncated = False

    try:
        entries = sorted(
            resolved.iterdir(), key=lambda p: p.name.casefold()
        )
    except OSError as exc:  # 权限不足等
        raise ConfigError(f"无法读取目录 {resolved}: {exc}") from exc

    for entry in entries:
        try:
            if entry.is_dir():
                dirs.append({"name": entry.name, "path": str(entry)})
                continue
            if entry.is_file() and entry.suffix.lower() in VIDEO_SUFFIXES:
                if len(files) >= MAX_LISTED_FILES:
                    truncated = True
                    continue
                files.append({"name": entry.name, "size": entry.stat().st_size})
        except OSError:
            # 坏掉的 symlink、没权限的子项：跳过这一个，别让整次浏览失败
            continue

    parent = resolved.parent
    return {
        "path": str(resolved),
        "parent": str(parent) if parent != resolved else None,
        "dirs": dirs,
        "files": files,
        "truncated": truncated,
        "total_files": len(files),
    }
