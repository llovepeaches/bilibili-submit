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


def test_setup_console_upgrades_cp1252_stream(monkeypatch):
    """复现云端打包的真实崩溃：PowerShell 下 stdout 是 cp1252。

    GitHub Actions 的冒烟测试就是这么炸的——``--help`` 渲染中文 help 时
    ``UnicodeEncodeError: 'charmap' codec can't encode characters``。
    """
    stream = io.TextIOWrapper(
        io.BytesIO(), encoding="cp1252", errors="strict", newline=""
    )
    monkeypatch.setattr(sys, "stdout", stream)
    console.setup_console()
    # 关键断言：中文必须能写出去（不抛 UnicodeEncodeError）
    sys.stdout.write("中文测试：投稿、封面上传\n")
    sys.stdout.flush()


class _TrackingStdout(io.TextIOWrapper):
    """能追踪实际写入字节的 cp1252 流。

    不能直接用 ``io.TextIOWrapper(BytesIO(), cp1252)``：``reconfigure()`` 会
    换掉内部 buffer，原 BytesIO 就再也收不到数据了。这里把 buffer 换成
    自己实现的可追踪对象，reconfigure 后依然能读到写入内容。
    """

    def __init__(self, encoding):
        self._chunks = []
        super().__init__(_Collector(self._chunks), encoding=encoding,
                         errors="strict", newline="")

    def written(self) -> str:
        return b"".join(self._chunks).decode("utf-8", "replace")


class _Collector(io.RawIOBase):
    """把写入的字节攒起来，供断言检查。

    必须实现完整的流接口：``reconfigure()`` 之后 TextIOWrapper 会调用
    ``readable()``，缺了会抛 AttributeError（argparse 渲染 help 时触发）。
    """

    def __init__(self, sink):
        self._sink = sink

    def writable(self):
        return True

    def readable(self):
        return False

    def seekable(self):
        return False

    def write(self, data):
        self._sink.append(bytes(data))
        return len(data)

    def flush(self):
        pass


def test_setup_console_survives_cp1252_argparse_help(monkeypatch):
    """完整复现崩溃路径：cp1252 控制台下 argparse 渲染 --help。

    这正是 GitHub Actions 冒烟测试的真实失败原因：
    ``UnicodeEncodeError: 'charmap' codec can't encode characters``，
    崩在 argparse 的 ``_print_message`` 里。

    修复前抛 UnicodeEncodeError、退出码 1；修复后正常退出 0。
    """
    from bilibili_submit import cli

    stream = _TrackingStdout("cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    console.setup_console()

    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0


def test_setup_console_handles_none_stream(monkeypatch):
    """PyInstaller 在某些窗口模式下 sys.stdout 为 None，不能崩。"""
    monkeypatch.setattr(sys, "stdout", None)
    console.setup_console()
    print("stdout 为 None 时也不能崩")
    sys.stdout = sys.__stdout__  # 复位，避免影响其他测试


def test_setup_console_is_idempotent():
    """重复调用不应破坏已正常的流。"""
    console.setup_console()
    before = sys.stdout
    console.setup_console()
    assert sys.stdout is before or sys.stdout.encoding.lower().startswith("utf")
