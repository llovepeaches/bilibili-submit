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
    """把控制台切到 UTF-8，并保证输出流永远不会因编码问题崩溃。

    必须尽早调用（早于 ``argparse`` 渲染 help）：``--help`` 与参数错误信息
    由 argparse 在 ``parse_args`` 内部直接写 ``sys.stdout``，此时若编码还是
    cp1252，渲染中文 help 会抛 ``UnicodeEncodeError``。

    三层保障：
    1. Windows 上切控制台代码页为 65001
    2. 把 stdout/stderr 重配置为 utf-8
    3. 重配置失败（管道重定向、PyInstaller 的 ``sys.stdout`` 为 None 等）
       时套一层 replace 兜底流，宁可显示问号也不崩
    """
    if is_windows():
        _switch_codepage()

    # 第 2、3 层：所有平台都做。非 Windows 上原本就是 utf-8，reconfigure
    # 幂等；这样也能覆盖 Windows 上前两步都失败的情况。
    for name in ("stdout", "stderr"):
        _harden_stream(getattr(sys, name, None), name)


def _switch_codepage() -> None:
    """Windows：把控制台代码页切到 UTF-8。"""
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


class _SafeStream:
    """编码不匹配时用 replace 兜底的文本流包装。

    argparse 的 ``_print_message`` 直接对 ``sys.stdout`` 调用 ``write``，
    绕过了 ``errors="replace"`` 的保护——它自己用 ``encoding`` 做 encode。
    这里包一层，写入时统一用 utf-8 + replace，从根上避免二次崩溃。
    """

    def __init__(self, stream, name: str) -> None:
        self._stream = stream
        self._name = name
        self.encoding = "utf-8"

    def write(self, data: str) -> int:
        if self._stream is None:
            return len(data)
        try:
            return self._stream.write(data)
        except UnicodeEncodeError:
            buf = getattr(self._stream, "buffer", None)
            if buf is None:
                return len(data)
            return buf.write(data.encode("utf-8", "replace"))

    def flush(self) -> None:
        if self._stream is not None:
            try:
                self._stream.flush()
            except Exception:  # noqa: BLE001
                pass

    def isatty(self) -> bool:
        try:
            return bool(self._stream is not None and self._stream.isatty())
        except Exception:  # noqa: BLE001
            return False

    def __getattr__(self, item):  # 透传其余属性
        return getattr(self._stream, item)


def _harden_stream(stream, name: str):
    """尽量把流重配置为 utf-8；实在不行就套 _SafeStream。"""
    if stream is None:
        # PyInstaller 在某些窗口模式下 sys.stdout 为 None，直接给个空实现，
        # 否则 print() 会 AttributeError
        sys.__dict__[name] = _SafeStream(None, name)
        return

    encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
    if encoding in ("utf8", "utf_8"):
        return  # 已经是 utf-8，不必动

    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
        return
    except Exception as exc:  # noqa: BLE001
        logger.debug("reconfigure %s 失败，改用兜底流: %s", name, exc)

    # reconfigure 不可用（老版本 Python、被包装的流、管道等）→ 套兜底
    sys.__dict__[name] = _SafeStream(stream, name)


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
