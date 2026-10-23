"""Linux 打包脚本（``build_linux.sh``）的守卫。

与 ``test_packaging.py`` 里盯 ``build_windows.bat`` 那几条是同一套思路：
不打真的跑一遍 PyInstaller（太慢，且要目标平台），而是把**脚本与
spec / 代码之间的约定**固定下来——那些约定一旦破掉，产物照样能生成，
坏在用户手里才现形。

特别盯三件只在 Linux 上成立的事：

1. **可执行位**。Windows 上能不能跑只看扩展名，Linux 看属性位。
   打包链任何一步丢了 ``+x``，用户看到的是 ``Permission denied``，
   而它跟「ffmpeg 坏了」长得一模一样。
2. **换行符**。``.gitattributes`` 默认 ``text=auto``，Windows 上检出会
   把 shell 脚本变成 CRLF，于是 bash 报 ``bad interpreter``——报错里
   看不到 ``\\r``，看的人只会以为 bash 没装。
3. **FUSE**。AppImage 的运行依赖 libfuse2，Ubuntu 23.04+ 默认没有。
   所以打包本身要绕开它，产物也要有不需要它的退路。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BUILD_SH = ROOT / "build_linux.sh"
GITATTRIBUTES = ROOT / ".gitattributes"
SPEC = ROOT / "bili_submit.spec"


def _text() -> str:
    return BUILD_SH.read_text(encoding="utf-8")


def _step(number: int) -> str:
    """切出第 number 步（``echo [n/8]``）的正文。

    必须按步骤切，不能在全文里搜 ``exit 1``——脚本里到处都是，
    全文搜恒为真，那种守卫拦不住任何东西。
    """
    body = _text()
    begin = body.find(f"echo [{number}/8]")
    assert begin >= 0, f"build_linux.sh 里找不到 echo [{number}/8]"
    end = body.find(f"echo [{number + 1}/8]")
    return body[begin: end if end >= 0 else len(body)]


def _code_only(step: str) -> str:
    """剥掉 shell 注释行。

    本项目**第四次**踩同一个坑了（前三次见 ``_without_comments``、
    ``_directives``、``_strip_pascal_comments``）：注释为了讲清楚
    「为什么这么写」，**必然**要把那个关键词写出来——
    「用 install -m 755 而不是 cp」这句注释里就有 ``install -m 755``。
    在全文里搜关键词的话，把真正的代码删掉照样绿。

    同理，结尾那段给用户的提示里也写着 ``--appimage-extract-and-run``。
    所以按 step 切分和剥注释两件事要一起做。
    """
    return "\n".join(
        line for line in step.splitlines() if not line.strip().startswith("#")
    )


def _steps() -> list[str]:
    return [_step(n) for n in range(1, 9)]


# ---------------------------------------------------------------------------
# 脚本本身
# ---------------------------------------------------------------------------

def test_script_exists_and_is_executable():
    """脚本要真的带可执行位。

    「./build_linux.sh」是 README 里的用法，没有 +x 就得改成
    「bash build_linux.sh」——而 Git **会**保留 mode，所以这里红了
    说明是有人用 ``git update-index`` 动过，值得看一眼。
    """
    assert BUILD_SH.is_file(), "缺少 build_linux.sh"
    assert os.access(BUILD_SH, os.X_OK), (
        "build_linux.sh 没有可执行位——README 里的 ./build_linux.sh 会失败"
    )
    assert _text().startswith("#!/"), "缺少 shebang"


def test_script_uses_bash_strict_mode():
    """必须 ``set -euo pipefail``。

    没有 ``-e`` 的话，中间任何一步失败脚本都会继续往下走——于是
    「ffmpeg 没复制成功」也能一路走到「打包完成」，产出一个缺组件
    的 AppImage，而构建日志里全是绿色的 echo。
    """
    body = _text()
    assert "set -euo pipefail" in body, (
        "build_linux.sh 没开 set -euo pipefail：中间步骤失败会静默继续，"
        "产出一个缺组件的 AppImage 而日志全绿"
    )


def test_shell_scripts_are_pinned_to_lf():
    """``.gitattributes`` 必须把 ``*.sh`` 钉成 LF。

    仓库里那句 ``* text=auto`` 会在**Windows 上检出时**把文本文件统一
    转成 CRLF。shell 脚本一旦是 CRLF，bash 读到的 shebang 是
    ``#!/usr/bin/env bash\\r``，报的是：

        /usr/bin/env: 'bash\\r': No such file or directory

    那个 ``\\r`` 在终端里不显示，看到的人只会以为 bash 没装。而
    build_linux.sh 恰好是最可能被 Windows 上的贡献者改到的文件——
    它旁边的 build_windows.bat 就是 CRLF。
    """
    text = GITATTRIBUTES.read_text(encoding="utf-8")
    assert re.search(r"^\*\.sh\s+text\s+eol=lf\s*$", text, re.MULTILINE), (
        ".gitattributes 里没有 `*.sh text eol=lf`——"
        "`* text=auto` 会在 Windows 检出时把它转成 CRLF，"
        "shebang 带上 \\r 后 bash 报 'bash\\r': No such file or directory"
    )


def test_script_refuses_to_run_off_linux():
    """在非 Linux 上要停下来并指路，不要硬跑。

    PyInstaller 不是交叉编译器，在 macOS 上跑这个脚本会打出一个
    macOS 的二进制，名字却叫 ``*-x86_64.AppImage``——那是**能生成**的，
    所以不会有任何一步失败，只会得到一个错的产物。
    """
    step = _step(1)
    assert "uname -s" in step, "第 1 步应检查操作系统"
    assert "Linux" in step
    # 要给出另一条路，光说「不行」没用
    assert "build_windows.bat" in step, (
        "拒绝时要指到 Windows 的打包入口——不然在 Windows 上双击的人"
        "只看到一句「只能跑在 Linux 上」"
    )


def test_all_eight_steps_are_announced():
    """八步都要有 ``echo [n/8]``。

    ``_step()`` 靠这个切分。少了一步的话，对应的守卫会静默地切到
    别处去——比如第 8 步没了，ffmpeg 可执行位那条就会去扫文件末尾。
    """
    body = _text()
    for number in range(1, 9):
        assert f"echo [{number}/8]" in body, f"缺少第 {number} 步的 echo 标记"


# ---------------------------------------------------------------------------
# ffmpeg
# ---------------------------------------------------------------------------

def test_build_stops_when_ffmpeg_is_missing():
    """准备 ffmpeg 失败时必须停，不能只打一行警告继续。

    与 ``build_windows.bat`` 第 4 步同一条规矩（那边由
    ``test_build_bat_stops_when_ffmpeg_is_missing`` 钉住）。后果也是
    一样的：可能产出一个不带 ffmpeg 的图形界面版，用户装完发现
    cover:auto 用不了，而我们从头到尾不知道。
    """
    step = _step(4)
    assert "setup_ffmpeg.py" in step, "第 4 步应当在准备 ffmpeg"
    assert "fail" in step and "exit 1" in _text(), (
        "准备 ffmpeg 失败后必须 fail；只 echo 警告继续的话，"
        "可能静默产出一个不带 ffmpeg 的图形界面版"
    )
    # 逃生舱：否则一台装不了 ffmpeg 的机器连包都打不出来
    assert "ALLOW_NO_FFMPEG" in step, "要有显式跳过 ffmpeg 的开关"


def test_ffmpeg_is_copied_with_its_executable_bit():
    """ffmpeg 的落点必须带着可执行位。

    这是 Linux 独有的坑：Windows 上能否执行只看扩展名，Linux 看属性位。
    用 ``cp`` 会继承源文件权限，而源文件可能来自一个不保权限的解压
    过程；打进 squashfs 之后它就是个 mode 644 的普通文件，运行时报
    ``Permission denied``——而那句报错跟「ffmpeg 坏了」一模一样。
    """
    # 只看第 4 步、且只看注释外的代码：第 4 步的注释里正写着
    # 「用 install -m 755 而不是 cp」，全文搜的话删掉真正的调用照样绿。
    step = _code_only(_step(4))
    assert re.search(r"install\s+-m\s+755", step), (
        "复制 ffmpeg 应用 install -m 755——cp 会继承源文件权限，"
        "丢掉 +x 之后运行时只报 Permission denied，看不出是权限问题"
    )


def test_verification_step_checks_the_executable_bit():
    """第 8 步要真的判一次 ffmpeg 可执行位。

    「备好了」不等于「落进去了」，「落进去了」也不等于「可执行」。
    前两步都有各自的守卫，这一条盯的是最后一道：产物目录里那个
    ffmpeg 到底能不能跑。缺了它，上面的 install -m 755 被改成 cp 也
    照样绿。
    """
    step = _step(8)
    assert '-x "$dir/ffmpeg"' in step, (
        "第 8 步没有判 ffmpeg 的可执行位——"
        "这是 Linux 上唯一一次能在发出去之前发现「ffmpeg 不可执行」的机会"
    )
    assert "[OK]" in step or "校验通过" in step


def test_ffmpeg_is_not_put_inside_internal():
    """ffmpeg 不能放进 ``_internal/``。

    与 Windows 安装版同一条约定：``_internal/`` 是 PyInstaller 的地盘，
    用户改不了那里。放在可执行文件同目录，用户才能自己换 ffmpeg 版本。
    Linux 上这点更关键——AppImage 是**只读**的 squashfs，放错位置
    连手动改的机会都没有。
    """
    body = _text()
    assert not re.search(r"_internal/ffmpeg", body), (
        "ffmpeg 不要放进 _internal——AppImage 是只读 squashfs，"
        "放那里用户连手动换版本的机会都没有"
    )
    assert "$dir/ffmpeg" in body, "ffmpeg 应落在 onedir 产物的根目录"


# ---------------------------------------------------------------------------
# tkinter
# ---------------------------------------------------------------------------

def test_build_checks_tkinter_before_packaging_the_gui():
    """打包前必须确认 tkinter 可用，并给出发行版的装法。

    Linux 上 tkinter 由发行版单独提供（``python3-tk``），**pip 装不了**。
    缺了它 PyInstaller 不报错，只是安静地少打一份 tcl/tk——产出的
    GUI 双击无反应，而这个包文件齐全、体积正常、ffmpeg 也在，
    第 8 步那些「文件在不在」的校验一条都发现不了。
    """
    step = _step(2)
    assert "import tkinter" in step, "第 2 步没检查 tkinter 是否可用"
    # 报错要说清各发行版怎么装：看到这句的人并不知道 tkinter 要单独装
    for hint in ("python3-tk", "python3-tkinter", "pacman"):
        assert hint in step, f"第 2 步的提示里缺 {hint}"


def test_there_is_a_way_to_build_without_the_gui():
    """要有只打命令行版的开关。

    构建机（容器、CI、无桌面的服务器）常常既没有 tkinter 也没有 root
    去装它。没有这个口子的话，那台机器上**连命令行版都打不出来**——
    就从「静默产出坏包」变成了「根本构建不了」，同样糟糕。
    """
    body = _text()
    assert "NO_GUI" in body, (
        "没有 NO_GUI 开关——没装 python3-tk 的机器上连命令行版都打不出来"
    )
    # 开关要真的接到打包那一步上，光有个变量不算
    step = _step(6)
    assert "NO_GUI" in step, "第 6 步没有按 NO_GUI 跳过图形界面版"


def test_gui_artifact_is_checked_for_the_tk_runtime():
    """第 8 步要验 GUI 产物里真有 tcl/tk 运行时。

    这是最后一道：前两道（spec 里的 import 检查、脚本第 2 步的探测）
    都盯着「构建机有没有 tkinter」，而这一条盯的是「打进去没有」——
    两者之间还隔着 PyInstaller 的分析过程，那一步也可能把它丢了。
    """
    step = _step(8)
    # 判的是**失败动作**而不是那条 find 命令：把 `|| fail` 换成 `|| true`
    # 之后，find 依然在、脚本依然「查了」，只是查了不算数——只盯命令
    # 本身的话这种改法会一路绿（变异自检抓出来的假守卫）。
    assert re.search(r'\[\s*-n\s+"\$tk_hits"\s*\]\s*\|\|\s*fail', step), (
        "第 8 步没有真的因为缺 tcl/tk 而失败——"
        "GUI 缺 tkinter 时包是「文件齐全但一启动就静默退出」"
    )
    assert re.search(r"-name\s+'?(tcl|tk)", step), (
        "第 8 步没在产物里找 tcl/tk 运行时"
    )
    assert "_internal" in step


# ---------------------------------------------------------------------------
# AppImage 组装
# ---------------------------------------------------------------------------

def test_apprun_execs_the_real_binary():
    """``AppRun`` 必须 ``exec`` 真正的二进制。

    不 exec 的话 shell 会 fork 一层：退出码和信号都传不回调用方，
    ``./xxx.AppImage --version`` 拿不到真实退出码，CI 里那条
    「命令行版能不能跑」的校验也就失去了意义。
    """
    body = _text()
    assert "AppRun" in body, "AppDir 缺少 AppRun——AppImage 没有入口"
    assert re.search(r"^\s*exec\s+", body, re.MULTILINE), (
        "AppRun 没有 exec——不 exec 的话退出码传不回调用方"
    )


def test_appimage_is_built_without_needing_fuse():
    """打包自身要绕开 FUSE。

    ``appimagetool`` 是 AppImage，跑它也要 FUSE；而 Ubuntu 23.04+ 默认
    不装 libfuse2。于是「在最新发行版上打 Linux 包」第一件事就卡住，
    报错是 ``AppImages require FUSE to run``——不看文档根本不知道
    该装什么。``--appimage-extract-and-run`` 让它自己解开再跑。
    """
    # 只搜第 7 步的**代码**：这个参数在两处注释里也出现了（一处解释
    # 为什么需要它、一处是结尾给用户的排障提示），全文搜恒为真。
    step = _code_only(_step(7))
    assert "--appimage-extract-and-run" in step, (
        "appimagetool 没用 --appimage-extract-and-run——"
        "它自己也是 AppImage，在没有 libfuse2 的机器上根本跑不起来"
    )


def test_a_directory_build_is_shipped_as_the_fallback():
    """必须同时出一个目录版（tar.gz）。

    AppImage 不是在所有地方都能跑：容器里没有 FUSE、某些发行版的
    安全策略禁止挂载 squashfs、老内核不支持。这些环境的共同点是
    **用户解不开这个包**，所以给一个解压即用的目录版，而不是让他
    去装 libfuse2（有些环境根本装不了）。
    """
    body = _text()
    assert "tar.gz" in body, "没有目录版产物——AppImage 跑不了的环境就无包可用"
    assert re.search(r"tar\s+-czf", body), "目录版应当用 tar -czf 打包"


def test_both_cli_and_gui_are_built():
    """命令行版和图形界面版都要打。

    Linux 用户里有相当比例是服务器/无显示器场景（只跑 CLI），也有
    桌面用户（要 GUI）。只出一种的话另一半人只能从源码跑。
    """
    body = _text()
    for name in ("bilibili-submit-cli", "bilibili-submit-gui"):
        assert re.search(rf"EXE_NAME={name}\b", body), f"没有打包 {name}"
    # 两个都要进 AppImage 那一轮
    assert body.count("AppImage") >= 2


def test_spec_is_invoked_with_the_appimage_flag():
    """打 Linux 包必须带 ``APPIMAGE=1``。

    少了它 spec 会退回 onefile，产出的是一个单文件二进制而不是
    onedir 目录——AppDir 组装那步照样能跑（把一个文件塞进 usr/bin），
    只是得到的 AppImage 每次启动都要把整个归档解到 /tmp。
    """
    body = _text()
    assert re.search(r"APPIMAGE=1\s+EXE_NAME=", body), (
        "PyInstaller 调用里缺 APPIMAGE=1——spec 会退回 onefile"
    )
    assert "APPIMAGE" in SPEC.read_text(encoding="utf-8"), (
        "spec 不认 APPIMAGE 开关，脚本设了也没用"
    )


def test_appimage_is_checked_to_be_an_elf():
    """第 8 步要验 AppImage 的文件头是 ELF。

    AppImage 本质是「ELF 头 + squashfs」。下载到一半的文件、代理塞的
    HTML 错误页、被截断的产物都过不了这一关——而它们**体积也可能
    正好对得上**，光看 size 是发现不了的。
    """
    step = _step(8)
    assert "7f454c46" in step or "ELF" in step, (
        "第 8 步没有校验 AppImage 的 ELF 魔数——"
        "下载不完整或被劫持的产物会一路走到发布"
    )


# ---------------------------------------------------------------------------
# 与 spec 的一致性
# ---------------------------------------------------------------------------

def test_ffmpeg_staging_matches_the_spec_contract():
    """脚本往 exe 同目录放 ffmpeg，要和 spec「onedir 不内嵌」对得上。

    spec 里 APPIMAGE=1 会把 ``BUNDLE_FFMPEG`` 关掉（onedir 恒外置），
    所以 ffmpeg **必须**由脚本复制进去。两边只要有一边改了，
    产物就是一个没有 ffmpeg 的 AppImage——它能启动、界面正常，
    只有自动抽帧永远不可用。
    """
    spec = SPEC.read_text(encoding="utf-8")
    # spec 侧：onedir 下不允许内嵌
    assert "ONEDIR" in spec and "BUNDLE_FFMPEG" in spec
    assert re.search(r"if ONEDIR and _FFMPEG_EXPLICIT", spec), (
        "spec 里的 onedir/内嵌冲突检查不见了"
    )
    # 脚本侧：两个产物目录都要真的放一份
    body = _text()
    assert "stage_ffmpeg" in body, "脚本里没有统一的 ffmpeg 落点处理"
    for name in ("bilibili-submit-cli", "bilibili-submit-gui"):
        assert re.search(
            rf'stage_ffmpeg\s+"\$DIST/{name}"', body
        ), f"{name} 没有把 ffmpeg 放进产物目录"


@pytest.mark.parametrize("number", range(1, 9))
def test_every_step_can_fail_loudly(number):
    """每一步失败都得真的停下来（或明确降级并说明）。

    按步切分是为了让「某一步静默失败」无处可藏：全文搜 ``exit 1``
    恒为真，而某一步里根本没有失败处理时，只有逐条看才看得出来。
    """
    step = _step(number)
    # 降级也算一种明确处理，但必须留下痕迹（[i] / [!] / [OK]）
    has_fail = "fail" in step
    has_notice = bool(re.search(r"\[i\]|\[!\]|\[OK\]", step))
    assert has_fail or has_notice, (
        f"第 {number} 步既没有失败处理也没有降级说明——"
        "它出问题时脚本会静默继续"
    )


# ---------------------------------------------------------------------------
# CI
# ---------------------------------------------------------------------------

LINUX_YML = ROOT / ".github" / "workflows" / "build-linux.yml"


def test_ci_builds_linux():
    """要有一条 workflow 真的在 Linux 上打一遍包。

    本地打不了 Linux 包的人（在 Windows 上开发）占了多数，而 spec 里
    那些平台分支**只有真跑一遍才会被执行到**。没有 CI 的话，
    「Linux 打包」就是一句没人验证过的承诺。
    """
    assert LINUX_YML.is_file(), "缺少 .github/workflows/build-linux.yml"
    body = LINUX_YML.read_text(encoding="utf-8")
    assert "ubuntu-latest" in body
    assert "./build_linux.sh" in body, "workflow 没有真的调用 build_linux.sh"
    assert "upload-artifact" in body, "打完了不上传，产物拿不到"


def test_ci_can_actually_run_the_appimage_it_built():
    """冒烟那一步必须装 ``libfuse2``。

    AppImage 的运行依赖 FUSE，Ubuntu 24.04 起默认不装。于是「打出包来
    在本机上跑一下」这个最基本的验证会先失败，而报错是
    ``AppImages require FUSE to run``——它看起来像**打出来的包有问题**，
    排查方向直接跑偏到打包链上。
    """
    body = LINUX_YML.read_text(encoding="utf-8")
    assert "libfuse2" in body, (
        "CI 没装 libfuse2——冒烟那一步连 AppImage 都启动不了，"
        "而报错看起来像产物有问题"
    )


def test_ci_smoke_counts_windows_not_just_processes():
    """图形界面的冒烟要数**窗口**，不能只看进程还在不在。

    界面起不来时 GUI 版会安静退出（windowed 程序没有控制台，错误全
    丢），所以「进程还在」是个有意义的信号；但反过来「进程还在」也
    可能是界面卡在某个不可见状态。真的去 X 里数窗口才说明它出来了。
    """
    body = LINUX_YML.read_text(encoding="utf-8")
    assert "xdotool" in body, "冒烟没有真的去 X 里查窗口"
    assert "search --onlyvisible" in body
    # 光数窗口还不够：数为 0 时要真的失败
    assert "exit 1" in body, "窗口数为 0 时没有让 job 失败——等于没查"


def test_linux_workflow_is_valid_yaml():
    """workflow 要能被解析。

    YAML 缩进错了的表现是「job 根本不出现」，而不是报错——GitHub
    只显示「没有可运行的 job」，看的人会以为是 paths 没匹配上。
    """
    import yaml  # type: ignore[import-untyped]

    payload = yaml.safe_load(LINUX_YML.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    jobs = payload.get("jobs")
    assert isinstance(jobs, dict) and jobs, "workflow 里没有 job"
    # 打包要有 job 级超时：脚本内的超时是第二道，job 级兜底才是最后一道
    for name, job in jobs.items():
        assert "timeout-minutes" in job, (
            f"job {name} 没有设 timeout-minutes——卡住时会一直占着 runner"
        )


def test_script_survives_shellcheck_syntax_check():
    """脚本要能通过 ``bash -n``。

    打包脚本平时没人跑（只在发版时跑一次），语法错误可以潜伏很久。
    这一条相当于编译——比任何针对内容的断言都便宜，也更能拦住
    「改了一行少个引号」这类事故。
    """
    if not _has_bash():
        pytest.skip("环境里没有 bash")
    done = subprocess.run(
        ["bash", "-n", str(BUILD_SH)],
        capture_output=True, text=True,
    )
    assert done.returncode == 0, f"build_linux.sh 语法错误：\n{done.stderr}"


def _has_bash() -> bool:
    import shutil

    return shutil.which("bash") is not None
