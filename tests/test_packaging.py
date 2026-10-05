"""打包形态相关的测试。

这些测的是「打出来的包长什么样、程序在里面能不能找到东西」——
不是业务逻辑，而是**只有打包之后才会暴露**的那类问题。
它们不能真跑 PyInstaller（太慢、且需要 Windows），但可以把
「构建脚本与spec/代码之间的约定」固定下来。

约定清单（改打包方式时这几条要一起改）：

1. ``INSTALLER=1`` 走 onedir，产物是``dist/<EXE_NAME>/`` 目录；
2. ffmpeg **不进归档**，由构建脚本复制到 **exe 同目录**；
3. 用户数据（cookie / 界面偏好）在 ``~/.config/``，不在程序目录——
   装到 Program Files 后程序目录只读，数据写那里必炸；
4. ``installer.iss`` 的 ``BuildDir`` 必须与 ``EXE_NAME`` 同名。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bilibili_submit import ffmpeg  # noqa: E402

SPEC = ROOT / "bili_submit.spec"
ISS = ROOT / "installer.iss"
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"
INSTALLER_YML = ROOT / ".github" / "workflows" / "build-installer.yml"
BUILD_BAT = ROOT / "build_windows.bat"


# ---------------------------------------------------------------------------
# spec：打包形态
# ---------------------------------------------------------------------------

def _spec_text() -> str:
    return SPEC.read_text(encoding="utf-8")


def test_installer_flag_switches_to_onedir():
    """``INSTALLER=1`` 必须真的改变打包形态。

    写成flag 却不生效是最糟的一种：CI 全绿、打出来的还是单文件，
    安装器照编，只是装了个onefile 进去——用户拿到「安装版」却仍是
    每次启动解压 60MB。
    """
    text = _spec_text()
    assert 'ONEDIR = os.environ.get("INSTALLER") == "1"' in text, (
        "spec 必须读INSTALLER 环境变量"
    )
    assert "COLLECT(" in text, "onedir 需要 COLLECT 来组装目录，缺了就不是 onedir"
    assert "exclude_binaries=onedir" in text, (
        "onedir 的 EXE 必须 exclude_binaries=True，否则二进制会被打进 exe"
    )


def test_onefile_and_onedir_share_the_same_exe_config():
    """两种形态的 EXE 参数只能有 ``exclude_binaries`` 一处差别。

    写成两份 ``EXE(...)`` 的话，以后调个图标 / console 很容易只改
    一处，于是「安装版」和「便携版」行为悄悄分叉——这类 bug 极难
    发现，因为两边都能跑，只是行为不一致。
    """
    text = _spec_text()
    assert text.count("EXE(") == 1, (
        f"EXE( 出现 {text.count('EXE(')} 次（不含 PYZ/COLLECT 那些），"
        "应该只有 _make_exe() 里一处。写了两份 EXE(...) 的话，"
        "改样式时会只改一处，onefile 与 onedir 行为分叉"
    )
    assert "def _make_exe():" in text, "EXE 配置应该抽成函数供两种形态共用"
    # onedir 的差异必须由这个变量驱动，而不是写死
    assert "[] if onedir else a.binaries" in text, (
        "onedir 的 binaries 应为空并交给 COLLECT"
    )


def test_installer_mode_refuses_to_embed_ffmpeg():
    """``INSTALLER=1`` + ``BUNDLE_FFMPEG=1`` 必须报错，不许静默取一个。

    onedir 唯一的优势就是启动时不解压 ffmpeg。内嵌等于把这个好处
    抵消掉——但更糟的是它**看起来能build 成功**，问题要到用户那里
    才暴露成「为什么安装版还是这么慢」。当场报错最省事。
    """
    env = {
        **os.environ,
        "GUI": "1", "INSTALLER": "1", "BUNDLE_FFMPEG": "1",
        "EXE_NAME": "x", "PYTHONDONTWRITEBYTECODE": "1",
    }
    # 只执行到 Analysis 之前——冲突检查在那之前就该 SystemExit
    proc = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm"],
        cwd=ROOT, capture_output=True, text=True, env=env, timeout=300,
    )
    combined = proc.stdout + proc.stderr
    assert "incompatible" in combined.lower(), (
        "INSTALLER + BUNDLE_FFMPEG 的冲突必须被明确拒绝，"
        f"实际输出：{combined[-500:]}"
    )


# ---------------------------------------------------------------------------
# ffmpeg 在 onedir 下的定位
# ---------------------------------------------------------------------------

def test_ffmpeg_next_to_exe_wins_over_system_path(tmp_path):
    """exe 同目录的 ffmpeg 必须优先于系统 PATH。

    这是 onedir 方案成立的前提：把 ffmpeg 放 exe 同目录，用户就能自己
    换版本（代码里本来就优先找这个位置）。而安装版装在 Program Files
    下，用户**没有权限**往那里写文件——放错了就是「自动抽帧用不了」
    且用户无从修正。
    """
    exe_dir = tmp_path / "app"
    exe_dir.mkdir()
    local = exe_dir / "ffmpeg.exe"
    local.write_text("#!/bin/sh\n", encoding="utf-8")

    with mock.patch.object(sys, "frozen", True, create=True), \
            mock.patch.object(sys, "executable", str(exe_dir / "app.exe")), \
            mock.patch("shutil.which", return_value=r"C:\elsewhere\ffmpeg.exe"):
        info = ffmpeg.find_ffmpeg(required=False)

    assert info is not None, "应该找到 ffmpeg"
    assert info.source == "bundled", f"应命中 exe 同目录，实际来源 {info.source}"
    assert Path(info.path) == local


def test_ffmpeg_embedded_path_is_not_where_onedir_puts_it():
    """onedir 的 ffmpeg **不该**在 ``_internal`` 里。

    onedir 下 ``sys._MEIPASS`` 指向 ``_internal/``，所以「内嵌」位置是
    ``_internal/ffmpeg.exe``。构建脚本如果把 ffmpeg 放那儿，程序仍能
    找到（embedded 分支），但**用户改不了**——而放exe 同目录才是
    onedir 的意义所在。这条测试把该放的位置钉死。
    """
    text = RELEASE_YML.read_text(encoding="utf-8") + BUILD_BAT.read_text(encoding="utf-8")
    # 必须有「复制到 dist\<EXE_NAME>\ffmpeg.exe」的动作
    assert re.search(r'ffmpeg\.exe"?\s*\$?\(?"?\$?dir', text, re.IGNORECASE) or \
        'ffmpeg.exe" "$dir\\ffmpeg.exe"' in text or \
        'ffmpeg.exe" dist\\bilibili-submit-gui\\ffmpeg.exe' in text, (
        "构建脚本必须把 ffmpeg.exe 复制到 onedir 产物的 exe 同目录"
    )
    # 不该复制到 _internal 里
    assert "_internal\\ffmpeg.exe" not in text and "_internal/ffmpeg.exe" not in text, (
        "ffmpeg 不要放进 _internal——那里用户改不了，等于放弃了 onedir 的好处"
    )


# ---------------------------------------------------------------------------
# 用户数据路径：装到 Program Files 后必须还能写
# ---------------------------------------------------------------------------

def test_user_data_lives_outside_the_program_directory():
    r"""用户数据必须在 ``~/.config/``，不能在程序目录。

    装到 ``C:\\Program Files\\`` 之后程序目录是**只读**的（普通用户
    没有写权限）。数据写在那里要么崩，要么被 UAC 静默重定向到
    ``C:\Users\X\AppData\Local\VirtualStore``——后者更糟：用户以为
    存在 A 处，重装后读的是 B 处，表现为「登录状态莫名丢了」。

    这条约束是安装版能成立的前提，改state/auth 时要一起看。
    """
    from bilibili_submit.config import DEFAULT_COOKIE_FILE
    from bilibili_submit.ui.state import ui_state_path

    for path in (DEFAULT_COOKIE_FILE, str(ui_state_path())):
        assert ".config" in path or "APPDATA" in path, (
            f"{path} 不在用户目录下；装到 Program Files 后写不进去"
        )
        # 不能是程序目录（源码运行时是仓库根，打包后是 exe 所在目录）
        assert "workspace" not in path and "dist" not in path, (
            f"{path} 落在程序目录里，打包后只读"
        )


# ---------------------------------------------------------------------------
# 安装器脚本与 spec 的一致性
# ---------------------------------------------------------------------------

def test_installer_iss_matches_the_build_output_name():
    """``installer.iss`` 的 BuildDir 必须等于 CI/脚本用的 EXE_NAME。

    两者对不上时ISCC **不会报错**——它会照抄 ``[Files]`` 里那条
    通配路径，装出一个缺文件的安装器，用户双击闪退才发现。
    """
    text = ISS.read_text(encoding="utf-8-sig")
    match = re.search(r'#define\s+BuildDir\s+"([^"]+)"', text)
    assert match, "installer.iss 缺少 BuildDir 定义"
    build_dir = match.group(1).strip("\\")

    for source, label in ((RELEASE_YML, "release.yml"), (BUILD_BAT, "build_windows.bat")):
        text = source.read_text(encoding="utf-8")
        uses_name = (
            "EXE_NAME: bilibili-submit-gui" in text
            or "EXE_NAME=bilibili-submit-gui" in text
        )
        assert uses_name, (
            f"{label} 应当用 EXE_NAME=bilibili-submit-gui 产出安装版目录"
        )
        assert build_dir.endswith("bilibili-submit-gui"), (
            f"installer.iss 的 BuildDir 末段是 {build_dir}，"
            "应当是 bilibili-submit-gui（与 EXE_NAME 一致）"
        )


def _directives(text: str, section: str) -> str:
    """取出某个段的**指令行**，丢掉注释和空行。

    分号开头的是注释——而 ``[Files]`` 段的注释里恰恰把
    ``recursesubdirs``「一定要开」写了一遍。直接在整段文本里
    ``in`` 搜索的话，把真正的指令删了测试还是绿的：注释替它
    作证。这是个真踩过的坑，所以两条段相关的断言都走这里。
    """
    if section not in text:
        return ""
    body = text.split(section)[-1].split("\n[")[0]
    return "\n".join(
        line for line in body.splitlines()
        if line.strip() and not line.strip().startswith(";")
    )


def test_installer_copies_the_whole_directory_recursively():
    """``[Files]`` 必须 ``recursesubdirs``。

    onedir 的绝大部分内容在 ``_internal/``（Python 运行时、tkinter
    的 tcl/tk 数据）。漏了这个 flag 时安装器只装顶层 exe，界面能
    出现、点一下就闪退——因为 ``import tkinter`` 找不到。
    """
    files = _directives(ISS.read_text(encoding="utf-8-sig"), "[Files]")
    assert "recursesubdirs" in files, (
        "[Files] 缺 recursesubdirs：_internal\\ 装不进去，程序会双击闪退"
    )


def test_installer_uses_a_stable_app_id():
    """必须有 ``AppId``。

    缺了它 Inno Setup 认不出是同一个程序：用户装新版时不会覆盖，
    而是并排装第二份，开始菜单出现两个图标，卸载时还会互删错文件。
    """
    text = ISS.read_text(encoding="utf-8-sig")
    assert "AppId=" in text, "installer.iss 缺 AppId，升级会装出两份"


def test_installer_does_not_delete_user_data_on_uninstall():
    """卸载**不能**删用户数据。

    cookie（登录状态）、界面偏好、投稿历史都在 ``~/.config/`` 下。
    删掉等于用户重装后必须重新扫码登录——这是最容易被「清理干净」
    这个念头害到的地方。程序目录的残留该删，用户数据不该。

    注意注释里**必须**留着「刻意不删」这句话：不然后人看到
    ``[UninstallDelete]`` 只删``_internal``，会以为漏了而补上，
    那才是真 bug。
    """
    text = ISS.read_text(encoding="utf-8-sig")
    body = _directives(text, "[UninstallDelete]")

    assert body, "[UninstallDelete] 段应该有实际指令（删 _internal 残留）"
    for danger in (".config", "bilibili_submit", "cookie", "USERPROFILE", "AppData"):
        assert danger not in body, (
            f"[UninstallDelete] 的指令里出现了 {danger}——"
            "卸载删掉用户数据会让重装后必须重新扫码登录"
        )
    assert "_internal" in body, (
        "[UninstallDelete] 应该清理程序目录里的 _internal 残留"
    )
    # 注释里要写明「刻意不删」，否则后人会当成漏了而补上
    section = text.split("[UninstallDelete]")[-1]
    assert "刻意" in section and "不删" in section, (
        "[UninstallDelete] 的注释里应写明「刻意不删用户数据」，"
        "免得后人以为漏了而补上——那才是真 bug"
    )


def test_readme_documents_both_gui_forms():
    """README 要同时说清「安装版」和「便携版」。

    便携版改名成 ``-portable`` 之后，旧的 ``bilibili-submit-gui.exe``
    这个名字就不存在了；README / CI 里漏改一处，用户点下载就是 404。
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "bilibili-submit-setup.exe" in readme, "README 没提安装器文件名"
    assert "installer.iss" in readme, "README 没提安装器脚本在哪"
    assert "bilibili-submit-gui-portable.exe" in readme, (
        "README 没提便携版的新文件名——旧的 bilibili-submit-gui.exe 已经不存在了"
    )


def test_ci_uploads_the_installer():
    """Release 必须把安装器传上去，否则编了没人拿得到。"""
    release = RELEASE_YML.read_text(encoding="utf-8")
    assert "bilibili-submit-setup.exe" in release, "release.yml 没上传安装器"
    assert "installer.iss" in release, "release.yml 没编译安装器"
    assert "innosetup" in release.lower(), "release.yml 没装 Inno Setup"
    # 安装器冒烟测试：只编译不装的话，"装完双击闪退"照样漏给用户
    assert "/VERYSILENT" in release, (
        "release.yml 没有静默安装测试——编译成功不等于装出来能用"
    )
    assert "unins000.exe" in release, "release.yml 没验证卸载器"
