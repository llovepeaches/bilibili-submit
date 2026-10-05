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


def _section(text: str, name: str) -> str:
    """按 ``[Name]`` **独占一行**切出段内容。

    不能用 ``text.split(name)[-1].split("\\n[")[0]``：注释里随手写一句
    「忘了 [Files] 就会装坏」就会把段名混进搜索结果，切出来的却是
    文件最后一段。本项目真踩过——``[Code]`` 段的注释里提了一句
    ``[Files]``，于是断言跑去 ``[Code]`` 里找 ``recursesubdirs``，
    报出一个和真实原因毫无关系的失败。
    """
    match = re.search(rf"^\[{re.escape(name)}\][ \t]*$", text, re.MULTILINE)
    if not match:
        return ""
    rest = text[match.end():]
    nxt = re.search(r"^\[[^\]]+\][ \t]*$", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _directives(text: str, section: str) -> str:
    """取出某个段的**指令行**，丢掉注释和空行。

    分号开头的是注释——而 ``[Files]`` 段的注释里恰恰把
    ``recursesubdirs``「一定要开」写了一遍。直接在整段文本里
    ``in`` 搜索的话，把真正的指令删了测试还是绿的：注释替它
    作证。这是个真踩过的坑，所以两条段相关的断言都走这里。
    """
    return "\n".join(
        line for line in _section(text, section).splitlines()
        if line.strip() and not line.strip().startswith(";")
    )


def test_section_parsing_ignores_section_names_inside_comments():
    """``_section`` 只认独占一行的段名，注释里提到段名不算数。

    这条不是假设出来的——``[Code]`` 段的注释里就写了句「照抄
    ``[Files]`` 的写法」，用 ``text.split("[Files]")[-1]`` 切出来
    成了 ``[Code]`` 的内容，于是 ``recursesubdirs`` 检查跑去
    Pascal 代码里找，报出一个和真实原因无关的失败。
    """
    text = "\n".join([
        "[Setup]",
        "AppId={{X}",
        "[Files]",
        "; 记得开 recursesubdirs",
        "Source: \"x\"",
        "[Code]",
        "; 这里提到了 [Files] 但它不算段名",
        "procedure Foo;",
    ])
    files = _section(text, "Files")
    assert "Source" in files, "该切出 [Files] 的内容"
    # 注释要保留（UninstallDelete 那条断言就靠它），但**不能**因为
    # 注释里的 [Files] 字样而把段定位到别处去
    assert "procedure" not in files, (
        "注释里的 [Files] 把段定位带偏了——切出来是 [Code] 的内容"
    )
    assert "procedure" in _section(text, "Code")

    # 光有内联样本不够，还得拿**真实的 installer.iss** 跑一遍：
    # 内联样本证明不了真实文件里没有别的干扰项。
    real = ISS.read_text(encoding="utf-8-sig")
    assert "[Files]" in _section(real, "Code"), (
        "真实 installer.iss 的 [Code] 段注释里应当提到 [Files]——"
        "这条断言拿它当反例，改注释时留意"
    )
    assert "recursesubdirs" in _directives(real, "Files"), (
        "段切分在真实文件上失效：没从 [Files] 段取到 recursesubdirs"
    )
    assert "function InitializeSetup" not in _section(real, "Files"), (
        "段切分被注释里的段名带偏：[Files] 段里混进了 [Code] 的内容"
    )


def test_installer_copies_the_whole_directory_recursively():
    """``[Files]`` 必须 ``recursesubdirs``。

    onedir 的绝大部分内容在 ``_internal/``（Python 运行时、tkinter
    的 tcl/tk 数据）。漏了这个 flag 时安装器只装顶层 exe，界面能
    出现、点一下就闪退——因为 ``import tkinter`` 找不到。
    """
    files = _directives(ISS.read_text(encoding="utf-8-sig"), "Files")
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


def test_ci_waits_for_the_installer_process():
    """静默安装必须 **等** 安装器结束，不能靠 ``$LASTEXITCODE``。

    安装器是 GUI 程序。PowerShell 里 ``& $setup /VERYSILENT`` 会立即
    返回、``$LASTEXITCODE`` **从不被赋值**，于是下一行
    ``if ($LASTEXITCODE -ne 0)`` 判成失败——CI 上真卡过，报错是
    ``静默安装失败 ()``（括号里空着，就是这个原因）。

    正确写法是 ``Start-Process -Wait -PassThru`` 再读 ``.ExitCode``。

    注意只查 PowerShell 的**注释外**代码——说明这段坑的注释里就得写
    ``& $setup`` 长什么样（不然后人看不懂），不能因为它出现在文件里
    就判失败。段名那个坑也栽在这儿，见 ``_section`` 的注释。
    """
    for path in (INSTALLER_YML, RELEASE_YML):
        lines = [
            line for line in path.read_text(encoding="utf-8").splitlines()
            # PowerShell 注释是 # 开头；YAML 的也是
            if not line.strip().startswith("#")
        ]
        code = "\n".join(lines)
        assert "& $setup" not in code, (
            f"{path.name} 用 & $setup 调安装器：GUI 程序不会等待，"
            "$LASTEXITCODE 拿不到值。改用 Start-Process -Wait -PassThru"
        )
        if "bilibili-submit-setup.exe" in code and "/VERYSILENT" in code:
            assert "Start-Process" in code, (
                f"{path.name} 静默安装没用 Start-Process——退出码判断会失效"
            )
            # 安装/卸载都得有超时：卡在 UAC 上会永远不返回。
            # -Wait 或轮询都行，但不能干等。
            assert "-Wait" in code or "while (-not" in code, (
                f"{path.name} 静默安装没有等待或超时保护——"
                "卡住时既拿不到退出码，也不会自己结束"
            )
            assert "超时" in code, (
                f"{path.name} 的安装/卸载没有超时保护：卡在 UAC 上会让 "
                "job 挂死，而挂死比失败难查得多"
            )
            # CI 上必须绕开 UAC：installer.iss 是 PrivilegesRequired=admin，
            # 静默安装时安装器会 fork 自己去提权，父进程立即退出、子进程
            # 等 UAC 确认——无人值守环境下就是永久挂起（真挂过 8 分钟）。
            assert "/CURRENTUSER" in code, (
                f"{path.name} 静默安装没有 /CURRENTUSER："
                "PrivilegesRequired=admin + /VERYSILENT 会让安装器 fork 自己"
                "去提权，CI 上永久挂起"
            )
        # 卸载器不能用 -Wait：卡在 UAC 或残留进程上会永远不返回。
        # CI 上真挂过 8 分钟，只能手动取消。要轮询 + 超时。
        assert '"/VERYSILENT" -Wait' not in code, (
            f"{path.name} 的卸载用了 -Wait：卸载器卡住时会永远不返回。"
            "改成轮询 + 超时"
        )
        # PowerShell 的续行符是反引号 ` 不是反斜杠。写成 \ 的话
        # 每行被当成独立命令，CI 上报的是
        # "The term '-ArgumentList' is not recognized"。
        #
        # 判据是「反斜杠**前面有没有空格**」：续行写作 `$setup \`
        # （反斜杠孤零零挂在行尾），而 Windows 路径写作
        # `stage-mini\`（反斜杠紧跟路径字符）。只判"行尾是反斜杠"
        # 会把一堆路径误判成续行。
        for line in lines:
            stripped = line.rstrip()
            if not stripped.endswith("\\") or len(stripped) < 2:
                continue
            assert not stripped[-2].isspace(), (
                f"{path.name} 里可能是用反斜杠续行的 PowerShell："
                f"{stripped.strip()[:50]}——续行符是反引号 ` 不是 \\"
                "（行尾反斜杠前带空格，正是续行的写法）"
            )
    assert "timeout-minutes" in INSTALLER_YML.read_text(encoding="utf-8"), (
        "build-installer.yml 应当设 timeout-minutes——"
        "脚本内的超时是第二道，job 级兜底才是最后一道"
    )


def test_code_paths_do_not_double_the_dist_prefix():
    """``[Code]`` 里 ``{src}`` 和 ``{#BuildDir}`` 不能直接拼在一起。

    两者的基准不同：

    - ``{src}`` = 安装器 exe 所在目录，也就是 ``dist\\``
    - ``[Files]`` 的 ``Source`` = 相对于 ``.iss`` 所在目录，也就是仓库根

    所以 ``'{src}\\' + '{#BuildDir}'`` 拼出来是 ``dist\\dist\\bilibili-
    submit-gui\\``，文件当然不存在。CI 上真踩过：``InitializeSetup``
    返回 False 让安装中止，而 ``/SUPPRESSMSGBOXES`` 又把 MsgBox 压掉了，
    表现为**无声挂起**——没有报错，job 就那么卡着。

    正确写法是用 ``ExtractFileName('{#BuildDir}')`` 取末段。
    """
    code = _section(ISS.read_text(encoding="utf-8-sig"), "Code")
    for line in code.splitlines():
        if "ExpandConstant" not in line:
            continue
        assert not (
            "{src}" in line and "{#BuildDir}" in line
            and "ExtractFileName" not in line
        ), (
            f"[Code] 把 {{#BuildDir}} 直接拼在 {{src}} 后面：{line.strip()[:60]}"
            "——{src} 已经是 dist\\，会拼成 dist\\dist\\..."
        )
        # 反过来也要成立：既然要取末段，就得真的取
        if "{src}" in line:
            assert "ExtractFileName" in line, (
                f"[Code] 用 {{src}} 拼路径却没取末段：{line.strip()[:60]}"
            )


def test_installer_uses_correct_event_prototypes():
    """``[Code]`` 段里事件函数的原型必须写对。

    这类错误**只有真跑 ISCC 才看得到**——CI 上真踩过：
    ``InitializeWizard`` 写成 ``function ... : Boolean``，ISCC 报
    ``Invalid prototype for 'InitializeWizard'``，而 installer.iss
    的其他部分一点问题都没有。ISCC 只能跑在 Windows 上，本地查不出。

    这两个最容易混：``InitializeSetup`` 是 function（返回 False 可以
    拒绝安装），``InitializeWizard`` 是 procedure（只做初始化，没有
    返回值）。名字像，写法不一样。
    """
    code = _section(ISS.read_text(encoding="utf-8-sig"), "Code")
    assert code, "installer.iss 应当有 [Code] 段"

    declared = {
        name: kind
        for kind, name in re.findall(
            r"^\s*(function|procedure)\s+(\w+)\s*[\(:;]", code, re.MULTILINE
        )
    }
    for name, expect in (("InitializeSetup", "function"),
                         ("InitializeWizard", "procedure")):
        if name in declared:
            assert declared[name] == expect, (
                f"{name} 写成 {declared[name]}，应该是 {expect}——"
                f"ISCC 会报 Invalid prototype for '{name}'"
            )

    # 检查器也得挡住同一件事（它比这条断言通用）
    assert "EVENT_PROTOTYPES" in (
        ROOT / "tools" / "check_installer.py"
    ).read_text(encoding="utf-8"), (
        "tools/check_installer.py 应当有事件原型表，"
        "否则这类错误只能等 ISCC 报——而那要跑一整轮 Windows CI"
    )


def test_installer_checker_survives_windows_console_encoding():
    """``tools/check_installer.py`` 在 Windows 控制台编码下不能崩。

    它打印中文（产物目录名、错误说明），而 Windows 上 Python 默认
    用控制台代码页：PowerShell 是 cp1252、cmd.exe 是 cp936，两者都
    编码不了部分中文——一句 ``print`` 就抛 UnicodeEncodeError，
    整个 job 变红，而真实的 installer.iss 一点问题都没有。

    **CI 上真发生过**：脚本在 Linux（UTF-8）跑得好好的，到 Windows
    runner 第一条 print 就把 job 打成 failure。一个检查 Windows
    安装器的脚本自己死在 Windows 编码上，是最不该有的失败方式。

    所以这里用子进程 + ``PYTHONIOENCODING`` 模拟两种控制台，确认它
    仍能正常退出。不模拟就测不出来——pytest 自己跑在 UTF-8 下。
    """
    import subprocess

    for codepage in ("cp1252", "cp936"):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "check_installer.py")],
            cwd=ROOT, capture_output=True,
            env={**os.environ, "PYTHONIOENCODING": codepage},
        )
        assert proc.returncode == 0, (
            f"check_installer.py 在 {codepage} 下退出了 "
            f"{proc.returncode}——Windows 控制台编码问题又回来了"
        )
        assert "UnicodeEncodeError" not in proc.stderr.decode(
            "utf-8", errors="replace"
        )


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
    body = _directives(text, "UninstallDelete")

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
    section = _section(text, "UninstallDelete")
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
