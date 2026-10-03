"""控制台编码适配。

Windows 控制台默认使用 GBK(cp936) 编码，而本程序会输出中文以及二维码用的
方块字符（``█▀▄``）——这些字符在 GBK 下无法编码，直接调用 print 会抛
``UnicodeEncodeError`` 让程序崩溃。本模块负责：

1. 把控制台代码页切到 UTF-8（``chcp 65001``），并把 stdout/stderr 重新配置为 utf-8
2. 探测当前终端是否真的能渲染二维码字符，不能则让二维码功能优雅降级
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger(__name__)

#: 二维码渲染需要的字符集，任一无法编码就不渲染
_BLOCK_CHARS = "█▀▄"


def is_windows() -> bool:
    return os.name == "nt" or sys.platform.startswith("win")


def setup_console() -> None:
    """在 Windows 上把控制台切到 UTF-8。其他平台无需处理。"""
    if not is_windows():
        return

    # 让 Windows 控制台按 UTF-8 解释输出（chcp 是 cmd 内建命令）
    done = False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        done = True
    except Exception as exc:  # noqa: BLE001 - 非交互式会话下没有控制台
        logger.debug("ctypes 设置控制台代码页失败: %s", exc)

    if not done:
        # 用 subprocess 而非 os.system：os.system 走 shell 重定向会在
        # 非 Windows 环境意外创建名为 nul 的文件
        try:
            subprocess.run(
                "chcp 65001", shell=True, capture_output=True, timeout=5
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("chcp 设置代码页失败: %s", exc)

    # Python 侧的流也要换成 utf-8，且用 replace 兜底避免二次崩溃
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.debug("重配置 %s 失败: %s", stream, exc)


def supports_block_chars() -> bool:
    """当前终端能否渲染二维码用的方块字符。"""
    encoding = getattr(sys.stdout, "encoding", None) or ""
    try:
        "".join(_BLOCK_CHARS).encode(encoding or "utf-8")
    except (LookupError, UnicodeEncodeError):
        return False
    return True


def can_print(text: str) -> bool:
    """探测某段文本能否被当前终端编码。"""
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        text.encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return False
    return True
