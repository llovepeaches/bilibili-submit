#!/usr/bin/env python3
"""把项目打包成可分发的源码 zip。

与直接 `zip -r` 的区别：
1. 排除 __pycache__、.pytest_cache 等垃圾目录
2. **把 .bat 转成 CRLF 换行**——Windows 的 cmd 解析多行 if/else 块时，
   LF 换行会导致解析异常。用户解压即用，不能依赖 git 事后转换
3. 保留 .git 目录，用户解压后可直接 git push，无需重新初始化仓库
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "bilibili-submit-source.zip"

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".codebuddy", "build", "dist", ".venv"}
EXCLUDE_NAMES = {"bilibili-submit-source.zip"}
# 这些文件必须是 CRLF，否则 Windows cmd 可能解析异常
CRLF_SUFFIXES = {".bat", ".cmd"}


def iter_files():
    for path in sorted(ROOT.rglob("*")):
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        if path.name in EXCLUDE_NAMES or path.suffix == ".pyc":
            continue
        if path.is_file():
            yield path


def main() -> int:
    converted = 0
    count = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in iter_files():
            arcname = path.relative_to(ROOT)
            if path.suffix.lower() in CRLF_SUFFIXES:
                try:
                    text = path.read_text(encoding="utf-8")
                    normalized = text.replace("\r\n", "\n").replace("\n", "\r\n")
                    zf.writestr(str(arcname), normalized.encode("utf-8"))
                    converted += 1
                    count += 1
                    continue
                except UnicodeDecodeError:
                    pass
            zf.write(path, arcname)
            count += 1

    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"打包完成: {OUT.name}  ({count} 个文件, {size_mb:.2f} MB)")
    print(f"  其中 {converted} 个 .bat 已转为 CRLF 换行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
