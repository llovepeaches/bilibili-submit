"""检查更新：查 GitHub 上本项目的最新 release，并在客户端内完成更新。

放在顶层模块而不是 ``ui/`` 下，是因为命令行的 ``check`` 也要用同一份
逻辑。整条更新链路分两段：

1. **发现**——查 release、比版本、按形态挑出该下载哪个资产；
2. **完成**——把资产下载到暂存目录，再按形态落地：安装版运行下载好的
   安装器（由它提权替换 Program Files 下的文件），便携版自行替换 exe
   并重启。两头都不经过浏览器：用户点「更新」，剩下的都在客户端里
   做完。

两条贯穿全模块的约定：

- **网络失败一律静默**。这是锦上添花的功能，不值得让「启动变慢」
  或「弹个错误框」成为它的代价。失败只记日志，调用方拿到 ``None``，
  界面上表现为「没新版」，而不是「出错了」。（下载是用户主动发起的，
  不属于这一类：它的失败必须让人看见，见 :func:`download_update`。）
- **状态自己管，不碰 ``ui.state``**。界面偏好那套属于 GUI 层，
  下层模块依赖它会把依赖方向倒过来。这里用一个独立的小文件，
  GUI 和 CLI 共用同一份，也就共用同一份限额。
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests

from . import __version__
from .exceptions import BiliError

logger = logging.getLogger(__name__)

__all__ = [
    "REPO",
    "REPO_URL",
    "RELEASES_URL",
    "API_URL",
    "CHECK_INTERVAL",
    "UpdateCancelled",
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
    "staging_dir",
    "pick_asset",
    "download_update",
    "replace_executable",
    "relaunch_app",
    "launch_installer",
    "cleanup_stale_updates",
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


class UpdateCancelled(Exception):
    """用户取消了更新（下载中点了取消）。

    与错误分开是因为取消**不该弹错误框**：界面安静地回到空闲态即可。
    单独一个异常类而不是复用 ``BiliError``——BiliError 的 ``hint``
    机制是给「怎么修」用的，取消没有什么要修的。
    """


#: 各形态优先下载的资产名。与 release.yml 里 ``gh release create``
#: 上传的资产名一一对应；改一边忘了另一边，表现是「查得到新版但
#: 下载不了」，而 :func:`pick_asset` 的测试钉不住跨文件的一致，
#: 只能靠人——所以两边都写了注释互相指路。
#:
#: - ``installed``：下载**安装器**。程序装在 Program Files 下，替换文件
#:   要管理员权限，只有安装器能体面地提权（UAC）并处理「文件正在使用」。
#: - ``portable``：下载**同名的新 exe**，自己换自己，不需要任何权限。
#: - ``source``：没有现成资产可换（源码没有「装」这回事），挑安装器
#:   只是为了让信息里有个可下载的东西，GUI 层对它另有引导。
ASSET_NAMES = {
    "installed": ("bilibili-submit-setup.exe",),
    "portable": (
        "bilibili-submit-gui-portable.exe",
        "bilibili-submit-cli-portable.exe",
    ),
    "source": ("bilibili-submit-setup.exe",),
}


@dataclass(frozen=True)
class ReleaseInfo:
    """一次检查结果。

    Attributes:
        version: 去掉 ``v`` 前缀的版本号，用于显示与判重。
        tag: GitHub 上的原始 ``tag_name``，用于比较（比较函数自己
            处理前缀，但留着原始值便于日志里查证）。
        url: 下载页。取不到 ``html_url`` 时回退到 release 列表页。
        prerelease: 是否为预发布版。默认不提示这类版本。
        asset_url: 供当前形态下载的资产直链（release 的
            ``browser_download_url``）。挑不到资产时为 ``None``。
        asset_name: 资产文件名，下载时用作暂存文件名。
        asset_size: 资产字节数（GitHub 报多少就存多少），用于校验
            下载完整性；取不到存 0，表示「不校验」。
    """

    version: str
    tag: str
    url: str
    prerelease: bool
    asset_url: str | None = None
    asset_name: str | None = None
    asset_size: int = 0


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

    asset_url, asset_name, asset_size = _asset_from_payload(payload, release_form())

    return (
        ReleaseInfo(
            version=tag.strip().lstrip("vV"),
            tag=tag.strip(),
            url=url,
            prerelease=bool(payload.get("prerelease", False)),
            asset_url=asset_url,
            asset_name=asset_name,
            asset_size=asset_size,
        ),
        True,
    )


def _asset_from_payload(
    payload: dict, form: str
) -> tuple[str | None, str | None, int]:
    """从 release JSON 里挑出当前形态该下载的资产 ``(直链, 文件名, 字节数)``。

    挑不到就全空——更新提示本身不依赖资产（界面上仍显示版本号和
    下载页），只是「客户端内更新」这条路走不了而已。
    """
    asset = pick_asset(payload.get("assets"), _asset_names(form))
    if asset is None:
        return None, None, 0
    size = asset.get("size")
    if isinstance(size, bool) or not isinstance(size, (int, float)):
        size = 0
    return asset.get("browser_download_url"), asset.get("name"), int(size)


def _asset_names(form: str) -> tuple[str, ...]:
    """这个形态优先下载哪些资产，按优先级排序。

    portable 多一步：把自己正在运行的 exe 名放在最前面——同一个
    release 里 GUI 便携版和 CLI 便携版都在，GUI 该拿 GUI 的，
    CLI 该拿 CLI 的。拿自己名字去对，比猜「这是 GUI 还是 CLI」可靠。
    """
    prefs = ASSET_NAMES.get(form, ASSET_NAMES["source"])
    if form != "portable" or not getattr(sys, "frozen", False):
        return prefs
    own = Path(sys.executable).name
    return (own, *prefs) if own else prefs


def pick_asset(
    assets: object, names: tuple[str, ...]
) -> dict | None:
    """按优先级从 release 的 ``assets`` 列表里挑一个。

    Args:
        assets: release JSON 里的 ``assets`` 字段，理论上是个字典列表。
            实际可能缺省、可能是别的类型——都当「没有可用资产」处理。
        names: 期望的资产名，靠前的优先。

    Returns:
        第一个名中了的资产字典（含 ``browser_download_url`` 等键），
        全都没中返回 ``None``。没有可用直链的资产会被跳过——
        下载必须走 ``browser_download_url``，api_url 要带 token。
    """
    if not isinstance(assets, list):
        return None
    by_name: dict[str, dict] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str) or not url:
            continue
        by_name[name] = asset
    for wanted in names:
        if wanted and wanted in by_name:
            return by_name[wanted]
    return None


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
    """弹窗文案，返回 ``(标题, 正文)``。按形态说明接下来会发生什么。"""
    title = f"发现新版本 {info.version}"
    form = release_form()
    if form == "installed":
        how = (
            "客户端会直接下载新安装包并运行它完成升级——不用打开浏览器、"
            "不用自己找文件。升级过程中客户端会退出一次，装完后从开始菜单"
            "重新打开即可。cookie 和投稿历史都在你的用户目录里，不受影响。"
        )
    elif form == "portable":
        how = (
            "客户端会直接下载新版并自动替换、重启——不用打开浏览器、"
            "不用自己找文件。旧版本会备份成 .old，出了问题也不会丢。"
        )
    else:
        how = (
            "源码运行没有「装」这回事，升级请重新拉取代码；"
            "也可以让客户端下载安装包，装一个独立的图形界面版，"
            "以后就能在客户端里直接更新了。"
        )

    return title, f"当前 {__version__}，最新 {info.version}。\n\n{how}"


def open_release_page(url: str | None = None) -> bool:
    """用系统默认浏览器打开下载页。返回是否成功打开。"""
    try:
        return bool(webbrowser.open(url or RELEASES_URL))
    except Exception as exc:  # noqa: BLE001 - 浏览器打开失败不该带崩界面
        logger.warning("打开下载页失败：%s", exc)
        return False


# ---------- 下载与应用更新 ----------


def staging_dir() -> Path:
    """下载暂存目录。

    刻意用系统临时目录而不是程序目录：半成品躺在程序旁边会被当成
    「新版已经就位」，而它还不能运行；暂存目录则一定可写（便携版
    可能在只读介质上）。
    """
    return Path(tempfile.gettempdir()) / "bilibili-submit-update"


def download_update(
    info: ReleaseInfo,
    *,
    dest_dir: Path | None = None,
    proxy: str | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    chunk_size: int = 64 * 1024,
) -> Path:
    """把新版下载到本地，返回下载好的文件路径。

    Args:
        info: 带 ``asset_url`` 的检查结果。
        dest_dir: 存放目录，默认 :func:`staging_dir`。
        proxy: 走设置页配的代理——查更新走它，下载也该走它，国内网络下
            这两步常常是同一个问题（沿用 ffmpeg 一键获取的同一取舍）。
        on_progress: ``(已下载字节, 总字节)`` 回调。总字节可能为 0
            （服务器没给 Content-Length 时）。
        is_cancelled: 周期性查询是否已取消；返回 True 时抛
            :class:`UpdateCancelled` 并清掉半成品。
        chunk_size: 分块大小，也是取消检查的粒度。

    Raises:
        UpdateCancelled: 用户取消。
        BiliError: 没资产可下、网络不通、服务器报错、下载不完整、
            或下载到的不是有效的程序文件。

    .. important::
       必须**在工作线程**调用：下载会阻塞几分钟（安装包 30~70MB），
       在主线程跑会把界面冻住。
    """
    if not info.asset_url:
        raise BiliError(
            "这个版本没有可下载的文件",
            hint=f"请手动打开下载页看看：{info.url}",
        )

    target_dir = dest_dir or staging_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / (info.asset_name or f"bilibili-submit-{info.version}.exe")
    part = final.with_suffix(final.suffix + ".part")

    session = _new_session(proxy)
    try:
        response = session.get(info.asset_url, stream=True, timeout=(5.0, 30.0))
    except requests.RequestException as exc:
        session.close()
        raise BiliError(
            "下载失败（网络不可达）", hint=f"{exc}\n可手动打开下载页：{info.url}"
        ) from exc

    try:
        if response.status_code != 200:
            # 资产直链会 302 到 objects.githubusercontent.com，requests
            # 默认跟随；真到不了 200 多半是链接失效或被拦。
            raise BiliError(
                f"下载失败：服务器返回 {response.status_code}",
                hint=f"可手动打开下载页：{info.url}",
            )
        total = int(response.headers.get("Content-Length") or info.asset_size or 0)
        done = 0
        try:
            with open(part, "wb") as fh:
                for chunk in response.iter_content(chunk_size=chunk_size):
                    if is_cancelled is not None and is_cancelled():
                        raise UpdateCancelled("下载已取消")
                    if not chunk:
                        continue
                    fh.write(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
        except BaseException:
            # 取消、写盘失败、迭代中断：半成品一律清掉，不留 `.part`
            # 垃圾，也别让下次误以为下载过
            part.unlink(missing_ok=True)
            raise
    finally:
        response.close()
        session.close()

    size = part.stat().st_size
    _verify_download(part, expected=info.asset_size or total, actual=size)
    os.replace(part, final)
    return final


def _verify_download(part: Path, *, expected: int, actual: int) -> None:
    """校验下载好的半成品：字节数对得上、文件头是 Windows 可执行魔数。

    两项都不过就删掉半成品再抛错——留着它比没有更危险（下次会被
    当成「下载过的缓存」直接用）。
    """
    if expected and actual != expected:
        part.unlink(missing_ok=True)
        raise BiliError(
            "下载不完整",
            hint=f"应有 {expected} 字节，实得 {actual} 字节；网络问题，重试即可",
        )
    with open(part, "rb") as fh:
        head = fh.read(2)
    if head != b"MZ":
        # Windows 可执行文件的魔数。HTML 错误页、代理劫持的占位文件
        # 都到不了这一步的字节数，所以它其实主要防「大小对得上但
        # 内容不对」的邪门情况。
        part.unlink(missing_ok=True)
        raise BiliError(
            "下载到的不是有效的程序文件",
            hint="多半是网络劫持或代理出错，换网络或关掉代理重试",
        )


def replace_executable(local: Path) -> Path:
    """便携版自替换：把新版换到运行中 exe 的位置，返回旧文件路径。

    顺序是刻意的：**先**把新版落到同目录的 ``.new``，**再**把运行中的
    exe 改名成 ``.old`` 腾出名字，**最后**才把 ``.new`` 改过来。前两步
    任意一步失败，原文件都还好好在原地——反过来做的话，中途失败会
    留下一个「程序不见了」的目录。

    Windows 上**允许改名运行中的 exe**（不允许的是删除），所以这套
    不需要任何权限；也正因如此，``.old`` 删不掉（自己还在跑），留给
    :func:`cleanup_stale_updates` 下次启动收尾。

    Raises:
        BiliError: 非便携版、或目标目录不可写（只读介质等）。
    """
    if not getattr(sys, "frozen", False):
        raise BiliError(
            "只有打包后的便携版支持自动替换",
            hint="源码运行请重新拉取代码，或下载安装包装一个图形界面版",
        )
    current = Path(sys.executable).resolve()
    if not current.is_file():
        raise BiliError("找不到正在运行的程序文件", hint=str(current))

    staged = current.with_name(current.name + ".new")
    old = current.with_name(current.name + ".old")
    try:
        if staged.exists():
            staged.unlink()
        if old.exists():
            old.unlink()
        shutil.move(str(local), str(staged))
        os.replace(current, old)
        os.replace(staged, current)
    except OSError as exc:
        # 只读介质（U 盘写保护之类）会走到这。尽力把 staged 还回去，
        # 别让程序目录少个文件；新版仍留在暂存目录，用户还能自己拷。
        try:
            if staged.exists() and not current.exists():
                os.replace(staged, current)
        except OSError:  # noqa: BLE001 - 恢复失败也只能到此为止
            pass
        raise BiliError(
            "无法替换程序文件",
            hint=f"{exc}\n新版已下载到 {local}，可手动复制替换",
        ) from exc
    return old


def relaunch_app(exe: Path | None = None) -> None:
    """启动（替换后的）程序。不等待——调用方紧接着就会退出自己。"""
    target = Path(exe) if exe is not None else Path(sys.executable)
    subprocess.Popen([str(target)], close_fds=True)  # noqa: S603 - 启动自己


def launch_installer(local: Path) -> None:
    """运行下载好的安装器。

    安装版装在 Program Files 下，替换文件要管理员权限——安装器会自己
    弹 UAC，比程序偷偷提权体面得多。调用方应在**退出前**调用本函数：
    进程一退，程序目录里的文件就不再被占用，安装器才能顺利覆盖。
    """
    if not local.is_file():
        raise BiliError("安装包不存在", hint=str(local))
    subprocess.Popen([str(local)], close_fds=True)  # noqa: S603 - 用户主动下载的文件


def cleanup_stale_updates() -> None:
    """清掉上次升级遗留的 ``.old`` / ``.new``。

    便携版升级时旧 exe 被改名成 ``.old``，而删除它的时候自己还在运行
    （Windows 不允许删运行中的文件），只能留给下一次启动收尾。
    失败不吭声：下次启动还会再试，不值得为一个残留文件报警。
    """
    if not getattr(sys, "frozen", False):
        return
    current = Path(sys.executable)
    for suffix in (".old", ".new"):
        stale = current.with_name(current.name + suffix)
        try:
            stale.unlink()
        except OSError:
            pass  # 还被占用或没权限：下次启动再试
