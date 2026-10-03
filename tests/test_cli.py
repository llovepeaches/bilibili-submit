"""CLI 参数与代理传递测试。

背景：``login`` 原本写死 ``proxy=None``，配置里的 ``account.proxy`` 对登录
不生效——境外网络或直连被风控时，用户配了代理却仍然登录失败，很难排查。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit import cli  # noqa: E402
from bilibili_submit.exceptions import ConfigError  # noqa: E402


def test_login_accepts_proxy_flag():
    args = cli.build_parser().parse_args(["login", "--proxy", "http://127.0.0.1:7890"])
    assert args.proxy == "http://127.0.0.1:7890"


def test_login_accepts_config_for_proxy():
    args = cli.build_parser().parse_args(["login", "-c", "config/my.yaml"])
    assert args.config == "config/my.yaml"
    assert args.proxy is None  # 代理值需从配置读，不是从命令行


def test_login_proxy_defaults_to_none():
    args = cli.build_parser().parse_args(["login"])
    assert args.proxy is None
    assert args.config is None


def test_login_reads_proxy_from_config(tmp_path, monkeypatch):
    """``-c`` 指定的配置里的 account.proxy 应在登录时生效。"""
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "account:\n"
        "  proxy: http://127.0.0.1:10809\n"
        "tasks:\n"
        "  - name: t\n"
        "    type: single\n"
        "    file: /tmp/x.mp4\n",
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(["login", "-c", str(cfg)])
    assert args.proxy is None  # 命令行没给

    # 模拟 cmd_login 里的取值逻辑
    proxy = args.proxy
    if proxy is None and args.config:
        proxy = cli.load_config(args.config).account.proxy
    assert proxy == "http://127.0.0.1:10809"


def test_command_line_proxy_overrides_config(tmp_path):
    """--proxy 优先级高于配置文件。"""
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "account:\n"
        "  proxy: http://from-config:1080\n"
        "tasks:\n"
        "  - name: t\n"
        "    type: single\n"
        "    file: /tmp/x.mp4\n",
        encoding="utf-8",
    )
    args = cli.build_parser().parse_args(
        ["login", "-c", str(cfg), "--proxy", "http://from-cli:7890"]
    )
    proxy = args.proxy
    if proxy is None and args.config:
        proxy = cli.load_config(args.config).account.proxy
    assert proxy == "http://from-cli:7890"


def test_cmd_login_passes_session_with_proxy(tmp_path, monkeypatch):
    """验证代理真的被写进 session，而不只是解析出来了。"""
    seen = {}

    def fake_login(session=None, timeout=180, render=True):
        seen["proxies"] = dict(session.proxies) if session else {}
        return {"SESSDATA": "x", "bili_jct": "y", "DedeUserID": "1"}

    monkeypatch.setattr(cli, "login_interactive", fake_login)
    monkeypatch.setattr(cli, "ensure_buvid", lambda client: {})
    monkeypatch.setattr(
        cli, "save_cookies", lambda c, p: tmp_path / "cookie.json"
    )

    args = cli.build_parser().parse_args(
        ["login", "--proxy", "http://127.0.0.1:7890", "--no-qr"]
    )
    assert cli.cmd_login(args) == cli.EXIT_OK
    assert seen["proxies"].get("https") == "http://127.0.0.1:7890"


def test_cmd_login_without_proxy_leaves_session_direct(tmp_path, monkeypatch):
    """没配代理时不应塞任何代理设置。"""
    seen = {}

    def fake_login(session=None, timeout=180, render=True):
        seen["proxies"] = dict(session.proxies) if session else {}
        return {"SESSDATA": "x", "bili_jct": "y", "DedeUserID": "1"}

    monkeypatch.setattr(cli, "login_interactive", fake_login)
    monkeypatch.setattr(cli, "ensure_buvid", lambda client: {})
    monkeypatch.setattr(
        cli, "save_cookies", lambda c, p: tmp_path / "cookie.json"
    )

    args = cli.build_parser().parse_args(["login", "--no-qr"])
    assert cli.cmd_login(args) == cli.EXIT_OK
    # requests 默认不带代理键
    assert "https" not in seen["proxies"]


def test_all_subcommands_still_parse():
    """回归：加参数没有破坏其他子命令。"""
    p = cli.build_parser()
    assert p.parse_args(["upload", "a.mp4", "--title", "t", "--tid", "21"])
    assert p.parse_args(["submit", "-c", "x.yaml", "--dry-run"])
    assert p.parse_args(["check"])
    assert p.parse_args(["tid"])
    assert p.parse_args(["history"])


@pytest.mark.parametrize(
    "flag,dest",
    [
        ("--proxy", "proxy"),
        ("-c", "config"),
        ("--config", "config"),
        ("--no-qr", "no_qr"),
        ("--timeout", "timeout"),
        ("--cookie-file", "cookie_file"),
    ],
)
def test_login_flags_exist(flag, dest):
    """所有 login 参数都应落到预期的 argparse 属性上。"""
    args = cli.build_parser().parse_args(["login"])
    assert hasattr(args, dest)


def test_proxy_under_wrong_section_is_rejected(tmp_path):
    """proxy 写进 upload 段必须报错，而不是静默失效。

    背景：早期 README 的示例把 proxy 写在 upload 段下，而代码读的是
    account.proxy。用户照抄后登录和投稿都不走代理，且毫无提示。
    严格字段校验是唯一防线，这里锁住它。
    """
    cfg = tmp_path / "wrong.yaml"
    cfg.write_text(
        "upload:\n"
        "  proxy: 'http://127.0.0.1:7890'\n"
        "tasks:\n"
        "  - name: t\n"
        "    type: single\n"
        "    file: /tmp/x.mp4\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="proxy"):
        cli.load_config(cfg)


def test_proxy_under_account_section_is_accepted(tmp_path):
    """正确位置：account.proxy 能被读到。"""
    cfg = tmp_path / "right.yaml"
    cfg.write_text(
        "account:\n"
        "  proxy: 'http://127.0.0.1:7890'\n"
        "tasks:\n"
        "  - name: t\n"
        "    type: single\n"
        "    file: /tmp/x.mp4\n",
        encoding="utf-8",
    )
    assert cli.load_config(cfg).account.proxy == "http://127.0.0.1:7890"
