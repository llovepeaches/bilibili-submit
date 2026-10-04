"""命令行入口。

    bili-submit login     扫码登录并保存 cookie
    bili-submit upload    单文件投稿（命令行参数优先于配置）
    bili-submit submit    按配置文件批量/定时投稿
    bili-submit check     校验配置、登录态与分区
    bili-submit tid       列出可选分区
    bili-submit gui       启动图形界面
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
    new_session,
    save_cookies,
)
from .client import BiliClient
from .config import (
    AppConfig,
    TaskConfig,
    expand_tasks,
    load_config,
    scan_video_files,
    task_dtime,
)
from .console import setup_console
from .exceptions import BiliError, ConfigError, NotLoggedInError
from .ffmpeg import ffmpeg_status, ffmpeg_version
from .metadata import COMMON_TIDS, ArchiveMeta
from .multipart import strip_part_marker
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
    p_login.add_argument(
        "-c",
        "--config",
        default=None,
        help="配置文件（读取其中的 account.proxy）",
    )
    p_login.add_argument(
        "--proxy", default=None, help="代理地址，如 http://127.0.0.1:7890"
    )

    # upload
    p_up = sub.add_parser(
        "upload",
        help="投稿视频（给多个文件即合并为一个稿件的多个分 P）",
    )
    p_up.add_argument(
        "file",
        nargs="+",
        metavar="FILE",
        help="视频文件路径或目录；给多个就合并成同一稿件的 P1/P2/P3，"
        "给目录则把目录里的视频都收进来（只收第一层）",
    )
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
    p_up.add_argument(
        "--part-title",
        action="append",
        default=None,
        dest="part_titles",
        metavar="TITLE",
        help="分 P 标题，按出现顺序对应各文件；可重复给，缺省用文件名",
    )
    # 互动设置与音质增强。default=None 是刻意的：命令行不指定时听配置文件的，
    # 不能因为没传参就把配置里开着的项悄悄关掉。所以这些开关只能「开」，
    # 想关请改配置文件（或留空让它走 defaults）。
    p_up.add_argument("--close-reply", action="store_true", default=None, help="关闭评论区")
    p_up.add_argument("--close-danmu", action="store_true", default=None, help="关闭弹幕")
    p_up.add_argument(
        "--selection-reply", action="store_true", default=None, help="开启精选评论"
    )
    p_up.add_argument(
        "--dolby",
        action="store_true",
        default=None,
        help="开启杜比音效（源文件本身得是杜比音轨，否则开了也没效果）",
    )
    p_up.add_argument(
        "--hires",
        action="store_true",
        default=None,
        help="开启 Hi-Res 无损音质（源文件本身得是无损音轨）",
    )

    # submit
    p_sub = sub.add_parser("submit", help="按配置文件执行投稿")
    p_sub.add_argument("-c", "--config", required=True, help="配置文件路径")
    p_sub.add_argument("--dry-run", action="store_true", help="只预览不实际投稿")

    # check
    p_chk = sub.add_parser("check", help="校验配置与登录态")
    p_chk.add_argument("-c", "--config", default=None, help="配置文件路径（可选）")

    # tid
    sub.add_parser("tid", help="列出分区 ID")

    # gui
    sub.add_parser("gui", help="启动图形界面")

    # history
    p_hist = sub.add_parser("history", help="查看本机投稿历史")
    p_hist.add_argument("--file", default=DEFAULT_HISTORY_FILE)

    return parser


def _configure_logging(verbose: bool) -> None:
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
    # 代理优先级：--proxy > 配置文件 account.proxy > 不走代理。
    # 登录必须支持代理——境外网络、或者直连被风控时，不走代理根本拿不到 cookie。
    proxy = args.proxy
    if proxy is None and args.config:
        proxy = load_config(args.config).account.proxy
    if proxy:
        print(f"使用代理：{proxy}")

    # new_session 自带浏览器 UA + Referer，缺了会被风控挡在 412。
    session = new_session(proxy)

    cookies = login_interactive(
        session=session, timeout=args.timeout, render=not args.no_qr
    )
    client = BiliClient(cookies=cookies, proxy=proxy)
    cookies.update(ensure_buvid(client))
    path = save_cookies(cookies, args.cookie_file)
    print(f"\nCookie 已保存到 {path}（权限 0600，请勿外传）")
    return EXIT_OK


def _expand_upload_args(items: list[str]) -> tuple[list[Path], list[str]]:
    """把命令行给的路径展开成文件列表——目录展开成它里面的视频。

    返回 ``(文件列表, 有问题的路径)``。

    目录**只扫第一层**：命令行 ``upload`` 的语义是「这一个目录就是一套
    视频」，钻进子目录会把不相关的东西也拉进同一个稿件。
    想要「每个子文件夹一个稿件」请用客户端或配置文件——命令行一次只投
    一个稿件，表达不了多稿件。
    """
    files: list[Path] = []
    problems: list[str] = []
    for item in items:
        path = Path(item).expanduser()
        if path.is_dir():
            found = scan_video_files(path)
            if not found:
                problems.append(f"{path}（目录里没有视频）")
                continue
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            problems.append(str(path))
    return files, problems


def cmd_upload(args: argparse.Namespace) -> int:
    cfg = load_config(args.config) if args.config else AppConfig()
    if args.no_resume:
        cfg.upload.resume = False

    sources = [Path(item).expanduser() for item in args.file]
    files, missing = _expand_upload_args(args.file)
    if missing:
        print(f"错误: 找不到可投稿的视频: {'、'.join(missing)}", file=sys.stderr)
        return EXIT_FAIL
    if not files:
        print("错误: 没有可上传的视频文件", file=sys.stderr)
        return EXIT_FAIL

    multip = len(files) > 1
    if args.part_titles and not multip:
        print("提示: 只有一个文件时 --part-title 不生效（没有分 P 可言）", file=sys.stderr)
    if args.part_titles and multip and len(args.part_titles) > len(files):
        print(
            f"提示: 给了 {len(args.part_titles)} 个分 P 标题但只有 {len(files)} 个文件，"
            "多出来的会被忽略",
            file=sys.stderr,
        )

    if args.title:
        title = args.title
    elif len(sources) == 1 and sources[0].is_dir():
        # 只给了一个目录：目录名就是用户给这套视频起的名字，
        # 比拿里面第一个文件的文件名当标题像样得多
        title = sources[0].name
    elif multip:
        # 多文件默认标题去掉尾部序号：旅行_01/02 → 「旅行」，
        # 而不是拿第一个文件的全名当整个稿件的标题
        base, _ = strip_part_marker(files[0].stem)
        title = base or files[0].stem
    else:
        title = files[0].stem

    task = TaskConfig(
        name=title,
        type="multip" if multip else "single",
        file=None if multip else str(files[0]),
        files=[str(path) for path in files] if multip else [],
        part_titles=list(args.part_titles or []) if multip else [],
        title=title,
        tid=args.tid,
        tag=args.tag,
        desc=args.desc,
        cover=args.cover,
        copyright=args.copyright,
        source=args.source,
        dtime=args.dtime,
        dtime_offset_hours=args.dtime_offset,
        up_close_reply=args.close_reply,
        up_close_danmu=args.close_danmu,
        up_selection_reply=args.selection_reply,
        dolby=args.dolby,
        hires=args.hires,
    ).merged(cfg.defaults)

    # 本地校验放在登录检查之前：参数填错不该让用户先去扫码登录
    task_dtime(task)
    ArchiveMeta(
        title=task.title or title,
        tid=int(task.tid or 21),
        tag=task.tag or "",
        copyright=int(task.copyright or 1),
        source=task.source or "",
        videos=[{"filename": "", "title": path.stem, "desc": ""} for path in files],
    ).normalized()

    if multip:
        print(f"将把这 {len(files)} 个文件合并为一个稿件的 {len(files)} 个分P：{title}")
        for index, path in enumerate(files, start=1):
            print(f"  P{index}: {path.name}")

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


def cmd_gui(args: argparse.Namespace) -> int:
    """启动图形界面。

    tkinter 是软依赖：精简版 Python 可能没带。缺了要给出可操作的提示，
    而不是抛一串 import traceback。
    """
    from .ui import gui_available, launch

    ok, reason = gui_available()
    if not ok:
        print(f"无法启动界面：{reason}", file=sys.stderr)
        print(
            "Windows / macOS 官方 Python 安装包默认带 tkinter；\n"
            "Linux 需要额外安装，例如：sudo apt install python3-tk",
            file=sys.stderr,
        )
        return EXIT_FAIL
    return launch()


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
    # 必须在 parse_args 之前切换编码：--help 与参数错误都由 argparse 在
    # parse_args 内部直接写 sys.stdout（cp1252 时渲染中文 help 会抛
    # UnicodeEncodeError），那时 _configure_logging 还没跑到。
    setup_console()

    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)

    handlers = {
        "login": cmd_login,
        "upload": cmd_upload,
        "submit": cmd_submit,
        "check": cmd_check,
        "tid": cmd_tid,
        "gui": cmd_gui,
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
