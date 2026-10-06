"""检查更新：查 GitHub 上本项目的最新 release，提示用户去下载。

放在顶层模块而不是 ``ui/`` 下，是因为命令行的 ``check`` 也要用同一份
逻辑。这里**只做发现和引导，不做自动下载**——安装版装在 Program Files
下，程序目录是只读的，程序没法替换自己的文件；便携版倒是可以直接
覆盖 exe，但让用户自己下载替换比程序偷偷改写自己更稳妥。

两条贯穿全模块的约定：

- **网络失败一律静默**。这是锦上添花的功能，不值得让「启动变慢」
  或「弹个错误框」成为它的代价。失败只记日志，调用方拿到 ``None``，
  界面上表现为「没新版」，而不是「出错了」。
- **状态自己管，不碰 ``ui.state``**。界面偏好那套属于 GUI 层，
  下层模块依赖它会把依赖方向倒过来。这里用一个独立的小文件，
  GUI 和 CLI 共用同一份，也就共用同一份限额。
"""

from __future__ import annotations

import json
import logging
import math
import os
import sys
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path

import requests

from . import __version__

logger = logging.getLogger(__name__)

__all__ = [
    "REPO",
    "REPO_URL",
    "RELEASES_URL",
    "API_URL",
    "CHECK_INTERVAL",
    "ReleaseInfo",
    "parse_version",
    "is_newer",
    "fetch_latest_release",
    "should_check",
    "check_for_update",
    "should_notify",
    "mark_version_seen",
    "release_form",
    "hint_text",
    "open_release_page",
    "state_path",
]

#: 仓库。与 ``installer.iss`` 的 ``AppURL`` 同源，由
#: ``tests/test_update.py::test_repo_matches_installer_iss`` 钉住。
REPO = "llovepeaches/bilibili-submit"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"

#: 两次检查之间的最小间隔。**不是**为了省流量：GitHub 对未认证请求
#: 限 60 次/小时/IP，装了这程序的多半在国内同一批出口 IP 上，一天一次
#: 既够用又不可能撞上限额。
CHECK_INTERVAL = 24 * 3600

#: 关掉检查的开关。离线机器和 CI 上不该有这个网络动作。
DISABLE_ENV = "BILI_UPDATE_CHECK"

#: 预发布后缀的先后。认不出的后缀排在已知后缀之前（更低）。
_PRE_ORDER = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2, "pre": 3}


@dataclass(frozen=True)
class ReleaseInfo:
    """一次检查结果。

    Attributes:
        version: 去掉 ``v`` 前缀的版本号，用于显示与判重。
        tag: GitHub 上的原始 ``tag_name``，用于比较（比较函数自己
            处理前缀，但留着原始值便于日志里查证）。
        url: 下载页。取不到 ``html_url`` 时回退到 release 列表页。
        prerelease: 是否为预发布版。默认不提示这类版本。
    """

    version: str
    tag: str
    url: str
    prerelease: bool


# ---------- 版本比较 ----------


def parse_version(text: str) -> tuple[tuple[int, int, int], str] | None:
    """把 ``v0.2.7-rc.1`` 拆成 ``((0, 2, 7), "rc.1")``。

    认不出来返回 ``None`` —— ``latest``、``nightly``、``1.x`` 这类 tag
    没法参与比较，宁可当作「没有新版」也不要拿字符串去比。
    """
    raw = (text or "").strip()
    if raw[:1] in ("v", "V"):
        raw = raw[1:]
    # build 元数据（PEP 440 的 +xxx）不参与先后判断
    raw = raw.split("+", 1)[0]
    core, _, pre = raw.partition("-")

    segments = core.split(".") if core else []
    if not segments:
        return None
    # 先校验**每一段**都是数字，再截断到三段。反过来的话
    # ``0.2.7.x`` 会被前三个合法段蒙过去，当成一个正常版本号。
    numbers: list[int] = []
    for segment in segments:
        if not segment.isdigit():
            return None
    for segment in segments[:3]:
        numbers.append(int(segment))
    # 不足三位补零。Windows 的版本资源用四段（0.2.6.0），多出来的段
    # 不参与比较——tag 上不会出现，真出现了也比报错好。
    while len(numbers) < 3:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2]), pre


def _pre_key(pre: str) -> tuple[int, int, int]:
    """预发布排序键，越大越「后」。正式版最大。"""
    text = (pre or "").strip().lower()
    if not text:
        return (1, 0, 0)
    name, _, rest = text.partition(".")
    level = _PRE_ORDER.get(name)
    if level is None:
        # 认不出的后缀：排在已知预发布之前，也就恒低于正式版
        return (0, -1, 0)
    return (0, level, int(rest) if rest.isdigit() else 0)


def is_newer(remote_tag: str, local: str, allow_prerelease: bool = False) -> bool:
    """远端 tag 是否比本地版本新。

    比较的是**整数元组**而不是字符串：按字符串比会得出
    ``"0.2.10" < "0.2.6"``，第十个修订版反而提示不出来。
    """
    remote = parse_version(remote_tag)
    mine = parse_version(local)
    if remote is None or mine is None:
        return False

    remote_key = _pre_key(remote[1])
    if not allow_prerelease and remote_key[0] == 0:
        # 预发布版默认不提示：它是给愿意踩坑的人准备的
        return False
    if remote[0] != mine[0]:
        return remote[0] > mine[0]
    return remote_key > _pre_key(mine[1])


# ---------- 拉取 ----------


def _new_session(proxy: str | None = None) -> requests.Session:
    """建一个只用于 GitHub 的会话。

    刻意**不复用** ``BiliClient``：它带着 B 站的 cookie、WBI 签名和
    伪装成 Chrome 的 UA，发给 GitHub 既不合适也不必要；而且它的
    ``_parse`` 只认 ``{"code": 0, ...}``，GitHub 的响应没有 ``code``
    字段，会被解析成 ``None``。

    UA 必须显式设置：**GitHub API 对没有 UA 的请求直接返回 403**。
    """
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": f"bilibili-submit/{__version__}",
            "Accept": "application/vnd.github+json",
        }
    )
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def _probe(
    proxy: str | None = None, timeout: tuple[float, float] = (5.0, 10.0)
) -> tuple[ReleaseInfo | None, bool]:
    """查一次，返回 ``(结果, 是否真的收到 HTTP 响应)``。

    第二项是给限频用的：连不上网时不该记「已检查」，否则一次断网
    会让接下来一整天都不再检查。
    """
    try:
        session = _new_session(proxy)
        try:
            response = session.get(API_URL, timeout=timeout)
        finally:
            session.close()
    except requests.RequestException as exc:
        # 超时 / DNS / 代理不通 / SSL 失败都在这里
        logger.warning("检查更新失败（网络不可达）：%s", exc)
        return None, False

    if response.status_code != 200:
        # 403 是撞了限频，404 是还没有 release。响应体结构各不相同，
        # 不去读它——读了也只能是给用户一句没用的话。
        logger.warning("检查更新失败：GitHub 返回 %s", response.status_code)
        return None, True

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning("检查更新失败：响应不是 JSON（%s）", exc)
        return None, True

    if not isinstance(payload, dict):
        logger.warning("检查更新失败：响应不是对象")
        return None, True

    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        logger.warning("检查更新失败：响应里没有 tag_name")
        return None, True

    url = payload.get("html_url")
    if not isinstance(url, str) or not url.strip():
        url = RELEASES_URL

    return (
        ReleaseInfo(
            version=tag.strip().lstrip("vV"),
            tag=tag.strip(),
            url=url,
            prerelease=bool(payload.get("prerelease", False)),
        ),
        True,
    )


def fetch_latest_release(
    proxy: str | None = None, timeout: tuple[float, float] = (5.0, 10.0)
) -> ReleaseInfo | None:
    """查最新 release。查不到（含网络不通）返回 ``None``。"""
    return _probe(proxy=proxy, timeout=timeout)[0]


# ---------- 限频与状态 ----------


def state_path() -> Path:
    """状态文件位置。跟着 cookie 走，卸载时不会被删。"""
    from .config import DEFAULT_COOKIE_FILE

    return Path(DEFAULT_COOKIE_FILE).expanduser().parent / "update-state.json"


def _read_state(path: Path | None = None) -> tuple[float, str]:
    """读 ``(上次检查时刻, 已提示过的版本)``。读不出来当没查过。"""
    target = path or state_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0.0, ""
    if not isinstance(raw, dict):
        return 0.0, ""

    last = raw.get("last_check")
    if isinstance(last, bool) or not isinstance(last, (int, float)):
        last = 0.0
    elif not math.isfinite(last) or last <= 0:
        last = 0.0

    seen = raw.get("seen_version")
    return float(last), seen if isinstance(seen, str) else ""


def _write_state(last_check: float, seen_version: str, path: Path | None = None) -> None:
    """写状态。写失败只记日志——限频记录丢了最多是下次多查一次。"""
    target = path or state_path()
    payload = {"last_check": last_check, "seen_version": seen_version}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target)
    except OSError as exc:
        logger.warning("更新检查状态写入失败（不影响使用）：%s", exc)


def should_check(last_epoch: float, now: float | None = None) -> bool:
    """距上次检查是否已超过 :data:`CHECK_INTERVAL`。"""
    now = time.time() if now is None else now
    if last_epoch <= 0:
        return True
    elapsed = now - last_epoch
    # elapsed < 0 是时钟被回拨过（或文件里存了未来的时间），
    # 按「没查过」处理，否则会永远卡在「刚查过」的状态里。
    return elapsed >= CHECK_INTERVAL or elapsed < 0


def _disabled() -> bool:
    """环境变量开关。给离线机器和 CI 用——它们不该有这个网络动作。"""
    return os.environ.get(DISABLE_ENV, "").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def check_for_update(
    proxy: str | None = None, force: bool = False, path: Path | None = None
) -> ReleaseInfo | None:
    """查一次更新，返回**值得提示**的新版本。

    返回 ``None`` 的几种情况都一样「没有可提示的东西」：被限频跳过、
    网络不通、已是最新、是个不想提示的预发布版。调用方不需要区分，
    也就不该区分——区分了就会有人想给「网络不通」弹个框。
    """
    if _disabled():
        return None

    last_check, _seen = _read_state(path)
    if not force and not should_check(last_check):
        return None

    info, got_response = _probe(proxy=proxy)
    now = time.time()

    if force or got_response:
        # 手动点的：无论成败都记，免得网络不通时连点变成反复请求。
        # 自动检查：只有真收到响应才记，断网那次不算数。
        _write_state(now, _seen, path)

    if info is None or not is_newer(info.tag, __version__):
        return None
    return info


def should_notify(version: str, path: Path | None = None) -> bool:
    """这个版本提示过了吗。用于「同一个新版只弹一次」。"""
    return _read_state(path)[1] != version


def mark_version_seen(version: str, path: Path | None = None) -> None:
    """记下「这个版本已经提示过」。"""
    _write_state(_read_state(path)[0], version, path)


# ---------- 形态与文案 ----------


def release_form() -> str:
    """当前这份程序是什么形态：``installed`` / ``portable`` / ``source``。

    判定依据是 onedir 的 ``_internal`` 在 exe **同目录**，而 onefile 的
    在临时解压目录里。不用 ``sys._MEIPASS`` 是因为它只在 onefile 下存在。
    """
    if not getattr(sys, "frozen", False):
        return "source"
    if (Path(sys.executable).parent / "_internal").is_dir():
        return "installed"
    return "portable"


def hint_text(info: ReleaseInfo) -> tuple[str, str]:
    """弹窗文案，返回 ``(标题, 正文)``。按形态给不同的升级办法。"""
    title = f"发现新版本 {info.version}"
    form = release_form()
    if form == "installed":
        how = (
            "程序装在 Program Files 下，目录只读，没法自己替换——"
            "下载新安装包重装一遍即可。cookie 和投稿历史都在你的用户目录里，不受影响。"
        )
    elif form == "portable":
        how = "下载新的 exe 覆盖旧文件就行；设置和 cookie 不在 exe 目录里，不会丢。"
    else:
        how = "用 pip 装的可以 pip install --upgrade 升级，或者直接下载安装包。"

    return title, f"当前 {__version__}，最新 {info.version}。\n\n{how}"


def open_release_page(url: str | None = None) -> bool:
    """用系统默认浏览器打开下载页。返回是否成功打开。"""
    try:
        return bool(webbrowser.open(url or RELEASES_URL))
    except Exception as exc:  # noqa: BLE001 - 浏览器打开失败不该带崩界面
        logger.warning("打开下载页失败：%s", exc)
        return False
