"""命令行入口。

    bili-submit login     扫码登录并保存 cookie
    bili-submit upload    单文件投稿（命令行参数优先于配置）
    bili-submit submit    按配置文件批量/定时投稿
    bili-submit check     校验配置、登录态与分区
    bili-submit tid       列出可选分区
    bili-submit history   查看本机投稿历史
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .auth import (
    DEFAULT_COOKIE_FILE,
    build_client_from_cookies,
    cookies_valid,
    ensure_buvid,
    load_cookies,
    login_interactive,
    save_cookies,
)
from .client import BiliClient
from .config import (
    AppConfig,
    TaskConfig,
    expand_tasks,
    load_config,
    task_dtime,
)
from .console import setup_console
from .exceptions import BiliError, ConfigError, NotLoggedInError
from .ffmpeg import ffmpeg_status, ffmpeg_version
from .metadata import COMMON_TIDS, ArchiveMeta
from .scheduler import DEFAULT_HISTORY_FILE, RunOptions, read_history, run_all, run_task
from .submit import get_backend

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bili-submit",
        description="哔哩哔哩自动投稿程序",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    # login
    p_login = sub.add_parser("login", help="扫码登录并保存 cookie")
    p_login.add_argument("--cookie-file", default=DEFAULT_COOKIE_FILE)
    p_login.add_argument("--timeout", type=int, default=180, help="等待扫码秒数")
    p_login.add_argument("--no-qr", action="store_true", help="不打印二维码，只显示链接")

    # upload
    p_up = sub.add_parser("upload", help="投稿单个视频文件")
    p_up.add_argument("file", help="视频文件路径")
    p_up.add_argument("--title", default=None, help="标题（默认取文件名）")
    p_up.add_argument("--tid", type=int, default=None, help="分区 ID")
    p_up.add_argument("--tag", default=None, help="标签，逗号分隔，最多 10 个")
    p_up.add_argument("--desc", default=None, help="简介")
    p_up.add_argument("--cover", default=None, help="封面图片路径，或 auto 自动抽帧")
    p_up.add_argument("--copyright", type=int, choices=[1, 2], default=None)
    p_up.add_argument("--source", default=None, help="转载来源（copyright=2 时必填）")
    p_up.add_argument("--dtime", type=int, default=None, help="定时时间戳（Unix 秒）")
    p_up.add_argument(
        "--dtime-offset", type=float, default=None, help="定时：距今多少小时后发布（需 >4）"
    )
    p_up.add_argument("-c", "--config", default=None, help="配置文件（可选，提供上传参数）")
    p_up.add_argument("--no-resume", action="store_true", help="禁用断点续传")

    # submit
    p_sub = sub.add_parser("submit", help="按配置文件执行投稿")
    p_sub.add_argument("-c", "--config", required=True, help="配置文件路径")
    p_sub.add_argument("--dry-run", action="store_true", help="只预览不实际投稿")

    # check
    p_chk = sub.add_parser("check", help="校验配置与登录态")
    p_chk.add_argument("-c", "--config", default=None, help="配置文件路径（可选）")

    # tid
    sub.add_parser("tid", help="列出分区 ID")

    # history
    p_hist = sub.add_parser("history", help="查看本机投稿历史")
    p_hist.add_argument("--file", default=DEFAULT_HISTORY_FILE)

    return parser


def _configure_logging(verbose: bool) -> None:
    setup_console()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _client_from_config(
    cfg: AppConfig, need_login: bool = True, allow_anonymous: bool = False
) -> BiliClient:
    """按配置构造客户端。

    Args:
        allow_anonymous: 允许在无 cookie 时返回匿名客户端，仅供 ``--dry-run``
            预览使用。
    """
    cookies = load_cookies(cfg.account.cookie_file)
    if not cookies_valid(cookies):
        if allow_anonymous:
            return BiliClient(proxy=cfg.account.proxy)
        raise NotLoggedInError(
            f"未找到有效 cookie（{cfg.account.cookie_file}），请先执行 login"
        )
    client = build_client_from_cookies(cookies, proxy=cfg.account.proxy)
    if not cookies.get("buvid3"):
        extra = ensure_buvid(client)
        if extra:
            cookies.update(extra)
            save_cookies(cookies, cfg.account.cookie_file)
    if need_login:
        client.require_login()
    return client


# ---------- 子命令 ----------


def cmd_login(args: argparse.Namespace) -> int:
    cookies = login_interactive(
        timeout=args.timeout, render=not args.no_qr
    )
    client = BiliClient(cookies=cookies, proxy=None)
    cookies.update(ensure_buvid(client))
    path = save_cookies(cookies, args.cookie_file)
    print(f"\nCookie 已保存到 {path}（权限 0600，请勿外传）")
    return EXIT_OK


def cmd_upload(args: argparse.Namespace) -> int:
    cfg = load_config(args.config) if args.config else AppConfig()
    if args.no_resume:
        cfg.upload.resume = False

    file = Path(args.file).expanduser()
    if not file.is_file():
        print(f"错误: 视频文件不存在: {file}", file=sys.stderr)
        return EXIT_FAIL

    task = TaskConfig(
        name=file.stem,
        type="single",
        file=str(file),
        title=args.title,
        tid=args.tid,
        tag=args.tag,
        desc=args.desc,
        cover=args.cover,
        copyright=args.copyright,
        source=args.source,
        dtime=args.dtime,
        dtime_offset_hours=args.dtime_offset,
    ).merged(cfg.defaults)

    # 本地校验放在登录检查之前：参数填错不该让用户先去扫码登录
    task_dtime(task)
    ArchiveMeta(
        title=task.title or file.stem,
        tid=int(task.tid or 21),
        tag=task.tag or "",
        copyright=int(task.copyright or 1),
        source=task.source or "",
    ).normalized()

    client = _client_from_config(cfg)
    outcome = run_task(
        client,
        task,
        cfg,
        backend=get_backend(cfg.submit.backend, cfg.submit.app),
        options=RunOptions(on_progress=None),
    )
    _print_outcome(outcome)
    if outcome.success and outcome.bvid:
        return EXIT_OK
    return EXIT_FAIL


def cmd_submit(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    tasks = expand_tasks(cfg)
    print(f"共 {len(tasks)} 个投稿任务")

    client = _client_from_config(
        cfg, need_login=not args.dry_run, allow_anonymous=args.dry_run
    )
    outcomes = run_all(client, tasks, cfg, options=RunOptions(dry_run=args.dry_run))

    ok = sum(1 for o in outcomes if o.success)
    print(f"\n完成 {ok}/{len(outcomes)}")
    for outcome in outcomes:
        if not outcome.success:
            print(f"  失败: {outcome.name} — {outcome.error}")
    return EXIT_OK if ok == len(outcomes) else EXIT_FAIL


def cmd_check(args: argparse.Namespace) -> int:
    if args.config:
        cfg = load_config(args.config)
        tasks = expand_tasks(cfg)
        print(f"配置 OK：{len(tasks)} 个任务")
        for task in tasks:
            tid = int(task.tid or 21)
            known = COMMON_TIDS.get(tid, "未知分区（仅告警）")
            print(f"  - {task.name}: {Path(task.file or '').name} → tid={tid} {known}")
    else:
        cfg = AppConfig()
        print("未提供配置文件，仅检查登录态")

    # 环境自检放在登录检查之前：未登录时这些信息恰恰最需要看到
    _print_ffmpeg_status()

    try:
        client = _client_from_config(cfg)
        info = client.nav()
        name = (info.get("uname") or info.get("mid") or "?") if info else "?"
        print(f"登录态 OK：{name}")
    except NotLoggedInError as exc:
        print(f"登录态检查失败：{exc}")
        return EXIT_FAIL

    return EXIT_OK


def _print_ffmpeg_status() -> None:
    """报告 ffmpeg 就绪情况——只影响 cover: auto，不影响其他功能。"""
    ffmpeg = ffmpeg_status()
    if ffmpeg is None:
        print("ffmpeg: 未找到（仅影响 cover: auto 自动抽帧，其他功能不受影响）")
        print("        修复：把 ffmpeg.exe 放到程序同目录，或 pip install imageio-ffmpeg")
        return
    version = ffmpeg_version(ffmpeg)
    if version != "未知":
        version = version.replace("ffmpeg version ", "").split(" ")[0]
    print(f"ffmpeg: 就绪（{ffmpeg.source}，版本 {version}）")


def cmd_tid(args: argparse.Namespace) -> int:
    print("常用分区（完整列表以服务端返回为准）：")
    for tid, name in sorted(COMMON_TIDS.items(), key=lambda kv: kv[0]):
        print(f"  {tid:<5} {name}")
    return EXIT_OK


def cmd_history(args: argparse.Namespace) -> int:
    entries = read_history(args.file)
    if not entries:
        print(f"暂无投稿历史（{args.file}）")
        return EXIT_OK
    print(f"投稿历史 {len(entries)} 条：")
    for entry in entries[-50:]:
        stamp = entry.get("time", 0)
        from datetime import datetime

        when = (
            datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
            if stamp
            else "?"
        )
        print(f"  {when}  {entry.get('bvid','')}  {entry.get('name','')}")
    return EXIT_OK


def _print_outcome(outcome: Any) -> None:
    if outcome.success:
        print(f"\n投稿成功：{outcome.bvid}\n{outcome.url}")
    else:
        print(f"\n投稿失败：{outcome.error}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    handlers = {
        "login": cmd_login,
        "upload": cmd_upload,
        "submit": cmd_submit,
        "check": cmd_check,
        "tid": cmd_tid,
        "history": cmd_history,
    }
    try:
        return handlers[args.command](args)
    except NotLoggedInError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return EXIT_FAIL
    except ConfigError as exc:
        print(f"配置错误: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    except BiliError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("\n已取消", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
