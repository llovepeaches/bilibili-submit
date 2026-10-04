"""环境自检：登录态与 ffmpeg 一把梭。

为什么要单独一个模块：这两样都不是「读一下内存里的变量」——
登录态要读 cookie 文件，ffmpeg 要探路径、必要时还要**启动子进程跑
``ffmpeg -version``**（最坏 10 秒超时）。这些活一旦放在 Tk 主线程，
界面就整个冻住：用户点开设置页看到的是程序死了，不是程序在忙。

所以这里只提供「纯检测」——进去的是 cookie 路径，出来的是一个数据
快照，一个 Tk 符号都不碰。谁要显示就自己拿快照去填控件，
且必须放到工作线程里跑（见 :mod:`.workers`）。

.. important::
   ``load_cookies`` 在一次检测里**只调一次**。早先的写法是
   ``ctx.logged_in`` 这个 property，在同一行里被求值两遍
   （``theme.tone(...) if ctx.logged_in else ...`` 和
   ``text=... if ctx.logged_in else ...``）——文件读了两遍，
   典型的「顺手写成这样」的浪费。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..auth import cookies_valid, load_cookies
from ..exceptions import BiliError
from ..ffmpeg import FfmpegInfo, ffmpeg_status, ffmpeg_version

__all__ = ["EnvironmentSnapshot", "probe_environment"]


@dataclass(frozen=True)
class EnvironmentSnapshot:
    """某一时刻的环境状态。

    Attributes:
        logged_in: cookie 是否有效。
        login_error: 读取/校验 cookie 时的错误文案；正常时为空串。
            刻意和 ``logged_in`` 分开而不是合成一个布尔——「文件不存在」
            和「文件坏了」都是未登录，但用户的下一步动作完全不同：
            前者去登录，后者要找出被什么程序改坏了这个文件。
        ffmpeg: 定位结果，None 表示没找到。
        ffmpeg_version: 版本号；未启用探测或未找到时为空串。
    """

    logged_in: bool
    login_error: str = ""
    ffmpeg: FfmpegInfo | None = None
    ffmpeg_version: str = ""


def probe_environment(
    cookie_file: str,
    *,
    include_ffmpeg_version: bool = False,
) -> EnvironmentSnapshot:
    """检测登录态与 ffmpeg。**纯函数，不碰 Tk，必须在工作线程调用。**

    Args:
        cookie_file: cookie 文件路径。
        include_ffmpeg_version: 是否读 ffmpeg 版本。这一步会
            ``subprocess.run`` 启动外部进程，状态栏这种只需知道
            「在不在」的地方就别开。
    """
    login_error = ""
    try:
        cookies = load_cookies(cookie_file)
        logged_in = cookies_valid(cookies)
    except BiliError as exc:
        logged_in = False
        login_error = str(exc)

    info = ffmpeg_status()
    version = ""
    if include_ffmpeg_version and info is not None:
        version = ffmpeg_version(info)

    return EnvironmentSnapshot(
        logged_in=logged_in,
        login_error=login_error,
        ffmpeg=info,
        ffmpeg_version=version,
    )
