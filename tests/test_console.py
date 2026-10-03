"""Windows 控制台编码适配测试。

重点是模拟 GBK 控制台——这是 Windows 默认编码，也是二维码渲染崩溃的根因。
"""

import io
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit import console  # noqa: E402
from bilibili_submit.auth import _render_qrcode  # noqa: E402


class _FakeStdout:
    """模拟不同编码的控制台（只用于不写输出的探测）。"""

    def __init__(self, encoding):
        self.encoding = encoding

    def write(self, _text):
        return 0

    def flush(self):
        pass


@pytest.fixture
def fake_stdout(monkeypatch):
    def _apply(encoding, real=False):
        if real:
            # 真实可写流：能验证"真的去写"时的编码失败
            stream = io.TextIOWrapper(
                io.BytesIO(), encoding=encoding, errors="strict", newline=""
            )
        else:
            stream = _FakeStdout(encoding)
        monkeypatch.setattr(sys, "stdout", stream)
        return stream

    return _apply


def test_gbk_cannot_render_qrcode(fake_stdout):
    """GBK 控制台无法编码方块字符，必须判定为不支持。"""
    fake_stdout("gbk")
    assert console.supports_block_chars() is False


def test_utf8_can_render_qrcode(fake_stdout):
    fake_stdout("utf-8")
    assert console.supports_block_chars() is True


def test_unknown_encoding_is_treated_as_unsupported(fake_stdout):
    fake_stdout("no-such-encoding")
    assert console.supports_block_chars() is False


def test_can_print_probes_text(fake_stdout):
    fake_stdout("gbk")
    assert console.can_print("中文") is True       # 中文 GBK 支持
    assert console.can_print("█▀") is False        # 方块不支持


def test_render_qrcode_degrades_gracefully(fake_stdout):
    """不支持时应返回 False 而不是抛异常——这是防止登录流程崩溃的关键。

    这里用真实可写流：qrcode 真的去写方块字符时必然触发 UnicodeEncodeError，
    函数必须捕获它并降级。
    """
    fake_stdout("gbk", real=True)
    assert _render_qrcode("https://example.com") is False


def test_render_qrcode_on_utf8(fake_stdout):
    fake_stdout("utf-8", real=True)
    rendered = _render_qrcode("https://passport.bilibili.com/test")
    # 有 qrcode 库时应渲染成功
    assert rendered is True


def test_setup_console_is_safe_on_posix():
    """非 Windows 环境下调用不应抛异常。"""
    if console.is_windows():
        pytest.skip("仅在非 Windows 环境验证")
    console.setup_console()  # 不抛异常即通过
