#!/usr/bin/env python3
"""把 ffmpeg 可执行文件复制到指定目录。

打包脚本用它把 ffmpeg 放到 exe 同目录（外置），运行时程序会自动发现。
直接从源码运行本项目时，也可以用它把 ffmmpeg 放到项目根目录。

    python tools/setup_ffmpeg.py --dest dist

查找顺序与 :func:`bilibili_submit.ffmpeg.find_ffmpeg` 一致：
imageio-ffmpeg 内置二进制 → 系统 PATH。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def locate() -> str | None:
    """找一个可用的 ffmpeg，返回其路径。"""
    try:
        import imageio_ffmpeg  # type: ignore[import-untyped]

        path = imageio_ffmpeg.get_ffmpeg_exe()
        if path and Path(path).exists():
            return path
    except Exception as exc:  # noqa: BLE001
        print(f"[i] imageio-ffmpeg 不可用: {exc}", file=sys.stderr)

    found = shutil.which("ffmpeg")
    if found:
        return found
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="复制 ffmpeg 到指定目录")
    parser.add_argument("--dest", default=".", help="目标目录（默认当前目录）")
    parser.add_argument("--name", default=None, help="目标文件名，默认按平台命名")
    parser.add_argument(
        "--force", action="store_true", help="目标已存在时也覆盖"
    )
    args = parser.parse_args()

    source = locate()
    if source is None:
        print(
            "[x] 未找到 ffmpeg。\n"
            "    方案一：pip install imageio-ffmpeg（会自动下载对应平台的二进制）\n"
            "    方案二：自行安装 ffmpeg 并加入 PATH 后重试\n"
            "    方案三：跳过——程序除 cover: auto 自动抽帧外不依赖 ffmpeg",
            file=sys.stderr,
        )
        return 1

    name = args.name or ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
    dest_dir = Path(args.dest)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name

    if dest.exists() and not args.force:
        print(f"[=] 已存在，跳过: {dest}")
        return 0

    shutil.copy(source, dest)
    try:
        dest.chmod(0o755)
    except OSError:
        pass  # Windows 上 chmod 语义有限，忽略

    size_mb = dest.stat().st_size / 1024 / 1024
    print(f"[OK] {source}")
    print(f"  -> {dest}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
