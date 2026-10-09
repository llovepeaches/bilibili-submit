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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bilibili_submit import ffmpeg  # noqa: E402

SPEC = ROOT / "bili_submit.spec"
ISS = ROOT / "installer.iss"
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"
INSTALLER_YML = ROOT / ".github" / "workflows" / "build-installer.yml"
VERIFY_ZH_PS1 = ROOT / "tools" / "verify_installer_zh.ps1"
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

    ``[ \\t]*$`` 里必须吃掉 ``\\r``：``$`` 只在 ``\\n`` 前成立，CRLF
    文件的行尾是 ``\\r\\n``，于是这个锚点在 CRLF 上**永远不匹配**，
    ``_section`` 返回空串、调用方以为段不存在而**静默放行**。
    ``.gitattributes`` 把 ``installer.iss`` 定成 ``eol=crlf``，
    Windows 上检出就是 CRLF——所以这不是假想。
    tools/check_installer.py 里有同一个函数、同一个修法，
    由 test_section_parsing_survives_crlf_line_endings 钉住。
    """
    match = re.search(rf"^\[{re.escape(name)}\][ \t\r]*$", text, re.MULTILINE)
    if not match:
        return ""
    rest = text[match.end():]
    nxt = re.search(r"^\[[^\]]+\][ \t\r]*$", rest, re.MULTILINE)
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
    # 真实文件里 "[Code]" 三个字还在——在末尾那段解释「为什么故意不写
    # [Code] 段」的注释里。段切分必须认出它**不是**段名：否则
    # _section(real, "Code") 会返回文件尾巴，任何针对 [Code] 的断言
    # 都变成对着一段注释做检查，一路绿到有人真的往里塞 {src}。
    assert "[Code]" in real, (
        "installer.iss 末尾应当还留着讲 [Code] 段的注释——这条断言拿它当反例"
    )
    assert not re.search(r"^\[Code\][ \t]*$", real, re.MULTILINE), (
        "installer.iss 现在没有 [Code] 段，这条断言才拿注释里的 [Code] 当"
        "反例。真要加回来，请换一个只出现在注释里的段名当反例"
    )
    assert _section(real, "Code") == "", (
        "注释里提到的 [Code] 被当成了真段——用 text.split('[Code]')[-1] "
        "切就会切出文件尾巴"
    )
    assert "recursesubdirs" in _directives(real, "Files"), (
        "段切分在真实文件上失效：没从 [Files] 段取到 recursesubdirs"
    )
    assert "check_installer.py" not in _section(real, "Files"), (
        "段切分被带偏：[Files] 段里混进了文件末尾的注释"
    )


def test_section_parsing_survives_crlf_line_endings():
    """段切分在 CRLF 文件上必须照样работа——否则**所有**段相关断言静默放行。

    这是本轮给``[Languages]`` 加检查时才撞出来的：``[ \\t]*$`` 这个锚点
    里，``$`` 只在 ``\\n`` 前成立，而 CRLF 的行尾是 ``\\r\\n``——中间夹着
    一个 ``\\r``，锚点在 CRLF 上永远不匹配。于是 ``_section`` 返回空串，
    调用方把「段不存在」当成「段里没有违规内容」放过去了：
    ``_check_files_section``、``_check_code_prototypes``、
    ``_check_languages`` 一次性全部失效，而且没有任何报错。

    为什么会踩到：``.gitattributes`` 里 ``installer.iss`` 是
    ``eol=crlf``，Windows 上检出就是 CRLF。也就是说这些检查在作者
    自己的Linux 上绿着，在每个人 Windows 上都是空转。

    这条守卫用内联的 CRLF 文本做样本，顺带把真实的 ``installer.iss``
    换成 CRLF 再切一遍——后者才是「真的有人这么干」的形态。
    """
    crlf = "\r\n".join([
        "[Setup]",
        "AppId={{X}",
        "[Files]",
        "; 注释里提到 recursesubdirs，但 _directives 会滤掉它",
        'Source: "x"; Flags: recursesubdirs',
        "[Code]",
        "procedure Foo;",
    ]) + "\r\n"

    files = _section(crlf, "Files")
    assert "Source" in files, (
        "CRLF 下 _section 切不出 [Files]——[ \\t]*$ 不匹配，段名锚点失效"
    )
    assert "procedure" not in files, "CRLF 下段尾判定失效，切到了下一个段"
    assert "procedure" in _section(crlf, "Code")
    # _directives 走的是 strip()，本来就不受行尾影响，这里一并确认
    assert "recursesubdirs" in _directives(crlf, "Files"), (
        "CRLF 下 _directives 把指令当成注释滤掉了"
    )

    # 真实文件换成 CRLF 再切一遍：内联样本证明不了真实段名带空格之类。
    # 比较的是「能不能切出**非空的段**」而不是逐字相等——CRLF 版每行
    # 尾部都多一个 \r，两边内容本来就不该一样；这里要守的是段名锚点在
    # CRLF 上匹配得上（切空了就等于该段的检查静默放行）。
    real_lf = ISS.read_text(encoding="utf-8-sig")
    real_crlf = real_lf.replace("\n", "\r\n")
    for section in ("Files", "Languages", "Tasks", "Icons", "Run"):
        for label, text in (("LF", real_lf), ("CRLF", real_crlf)):
            assert _section(text, section), (
                f"{label} 下切不出 [{section}] 段——段名锚点不认 \\r，"
                "该段的检查会全部静默失效"
            )
        # 切出来的内容除行尾外应当一致（段尾边界没跑偏）
        assert _strip_cr(_section(real_crlf, section)) == _section(real_lf, section), (
            f"CRLF 与 LF 下 [{section}] 段的内容不同——段尾判定跑偏了"
        )


def test_checker_also_survives_crlf_line_endings():
    """``check_installer.py`` 的段解析同样必须吃 ``\\r``。

    与上面那条是同一个 bug 的两处副本（检查器与测试各一份 ``_section``）。
    少改一处就等于留了个只在另一边复现的坑。

    ⚠️ 这条断言改过两版，两版都是**假守卫**，记下来免得再犯：

      第一版：只比「CRLF 上是否仍退出 0」。可installer.iss 当时恰好
      没有任何违规，``_section`` 整个失效也照样绿。

      第二版：造一份缺 ``recursesubdirs`` 的脚本，比退出码。可``[Files]``
      **后面还有别的段**，所以尾部锚点失效并不影响段头匹配，
      ``_section`` 照样切得出 ``[Files]``，两版都非 0，绿得毫无意义。

    现在的判据是**输出逐字比对**：把 ``installer.iss`` 换成 CRLF 之后，
    检查器该看到的段一个都不能少。段切分若在 CRLF 上失效，
    ``_check_files_section`` / ``_check_languages`` 拿到的就是空串，
    会被 ``if files_section:`` 短路掉，对应的信息行随之消失。

    说清严重性的边界：实测这份 installer.iss 即使段切分完全失效，
    输出依然完整、退出码依然是 0——因为它的段**首尾都有别的段**，
    尾部锚点坏掉不影响段头匹配。所以这里防的不是「现在正在流血」，
    而是「段名后面带空格、或者段在文件末尾时才会暴露」的那类改法。
    但 ``.gitattributes`` 明明把 installer.iss 声明成 ``eol=crlf``，
    守卫与声明必须一致——否则这个声明就是一句没人执行的承诺。
    """
    def _run(workdir: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(workdir / "tools" / "check_installer.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(workdir),
        )

    outputs: dict[str, str] = {}
    codes: dict[str, int] = {}
    for label, eol in (("LF", "\n"), ("CRLF", "\r\n")):
        workdir = tmp_installer_copy(eol=eol)
        try:
            done = _run(workdir)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        assert done.returncode == 0, (
            f"{label} 版上检查器就失败了，这说明副本本身有问题：\n{done.stdout}"
        )
        outputs[label] = done.stdout
        codes[label] = done.returncode

    assert outputs["LF"] == outputs["CRLF"], (
        "检查器在 CRLF 与 LF 下的输出不同——段切分不认 \\r，"
        "至少有一个基于段的检查被静默跳过了：\n"
        f"--- LF ---\n{outputs['LF']}\n--- CRLF ---\n{outputs['CRLF']}"
    )

    # 光「输出相同」还不够：两份输出都可能是「什么都没查」的残缺输出。
    # 所以钉住几个只有真的读到了段才会出现的关键行。
    for expected in ("产物目录", "主程序", "语言 chinese", "静态检查通过"):
        assert expected in outputs["CRLF"], (
            f"CRLF 版的检查器输出里没有「{expected}」——"
            "检查什么都没查到，等于没跑"
        )
    assert codes["CRLF"] == codes["LF"] == 0


def _strip_cr(text: str) -> str:
    """去掉行尾的 ``\\r``，让 CRLF 与 LF 的内容可比。

    切段的结果里每行都带着原样的行尾，所以直接比大小会永远不等——
    而「不等」是换行符造成的，不是段边界跑偏。这里只抹掉 ``\\r``，
    不动别的东西：真要是段切多了一行少了一行，照样比不出来。
    """
    return text.replace("\r\n", "\n")


def tmp_installer_copy(eol: str = "\r\n") -> Path:
    """把仓库复制成一份指定换行的最小副本，返回临时目录。

    复制的是**最小可用集**：检查器会去 ``ROOT/dist/`` 下找产物，
    而 dist\\ 不存在时它会打印「跳过产物校验」并正常退出，所以
    不需要 PyInstaller 的真实输出。

    ``eol`` 决定 ``installer.iss`` 的行尾——``.gitattributes`` 把它
    定成 ``eol=crlf``，所以 Windows 上检出就是 CRLF（而不是 CI 上
    这个 Linux runner 看到的 LF）。BOM 必须带上，检查器会查这一条。
    """
    workdir = Path(tempfile.mkdtemp(prefix="iss-crlf-"))
    (workdir / "tools").mkdir()
    (workdir / "installer_languages").mkdir()
    shutil.copy(
        ROOT / "tools" / "check_installer.py", workdir / "tools" / "check_installer.py"
    )
    text = ISS.read_text(encoding="utf-8-sig")
    # 源文件本身是 LF（.gitattributes 让 git 在检出时才展开成 CRLF），
    # 所以先统一成 LF 再按需替换成目标行尾。
    normalized = text.replace("\r\n", "\n")
    (workdir / "installer.iss").write_bytes(
        b"\xef\xbb\xbf" + normalized.replace("\n", eol).encode("utf-8")
    )
    for isl in (ROOT / "installer_languages").glob("*.isl"):
        shutil.copy(isl, workdir / "installer_languages" / isl.name)
    return workdir


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


# ---------------------------------------------------------------------------
# 安装向导的语言
# ---------------------------------------------------------------------------

ISL = ROOT / "installer_languages" / "ChineseSimplified.isl"


def test_installer_wizard_is_chinese():
    """安装向导必须是中文的。

    这是一条**回归守卫**，钉的是一个真实存在了整个0.2.x 生命周期的
    bug：``[Languages]`` 里写着 ``Name: "chinese"``（语言名叫 chinese，
    所以语言这一栏看着是对的），``MessagesFile`` 却指向
    ``compiler:Default.isl``——那是**英文**文件。装出来整个向导全是
    英文，而仓库里没有任何一个检查能发现「向导不是中文的」。

    ``Name`` 与 ``MessagesFile`` 各管一件事：前者是语言下拉框里显示的
    名字，后者决定实际文案。所以「语言名对」不代表「语言对」——
    这正是它能潜伏这么久的原因。
    """
    directives = _directives(ISS.read_text(encoding="utf-8-sig"), "Languages")
    assert directives, (
        "installer.iss 缺 [Languages] 段——Inno Setup 会退回英文向导"
    )

    # 逐个语言条目查，而不是只搜字符串：注释里提一句 MessagesFile
    # 就能骗过 `in`，而那正是 bug 的藏身处（注释解释了它）。
    entries = re.findall(
        r'Name\s*:\s*"([^"]*)"\s*;\s*MessagesFile\s*:\s*"([^"]*)"',
        directives,
    )
    assert entries, (
        "[Languages] 里没有成对的 Name/MessagesFile 指令："
        f"{directives!r}"
    )
    for name, messages_file in entries:
        bare = messages_file.lower().split(":", 1)[-1]
        assert bare != "default.isl", (
            f"语言 {name!r} 的消息文件是 Default.isl（英文）——"
            "装出来会是英文向导。指向 installer_languages\\ 下的中文翻译"
        )
        assert not messages_file.lower().startswith("compiler:"), (
            f"语言 {name!r} 用了 compiler: 前缀（{messages_file}）——"
            "它依赖构建机上 Inno Setup 的安装布局；本项目刻意把翻译"
            "vendored 进仓库，就是为了能对着它做检查"
        )
        assert "\\" in messages_file, (
            f"MessagesFile 应用相对路径指向仓库里的文件，实际是 {messages_file!r}"
        )

    # 光有配置还不够：那个文件必须真的在、真的是中文。
    assert ISL.is_file(), (
        f"翻译文件不在：{ISL.relative_to(ROOT)}。"
        "MessagesFile 指向一个不存在的文件时，ISCC 的表现取决于环境，"
        "本项目见过的最坏情况是静默退回英文"
    )


def test_installer_language_file_is_utf8_with_bom():
    """翻译文件必须是 UTF-8 **带 BOM**。

    与 ``installer.iss`` 同一个坑：ISCC 靠 BOM 认出 UTF-8，不带 BOM
    就按 ANSI（GBK / 1252）读，中文变成乱码——**而且不报错**，只在你
    用户眼前发生。官方那份``ChineseSimplified.isl`` 本身是不带 BOM 的
    （UTF-8 无 BOM），直接用就是坑，所以落库时必须补上。
    """
    raw = ISL.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), (
        "installer_languages/ChineseSimplified.isl 必须是 UTF-8 with BOM，"
        "否则 ISCC 按 ANSI 读，中文乱码且不报错"
    )
    text = raw.decode("utf-8-sig")
    assert "\ufffd" not in text, (
        "翻译文件里有 U+FFFD 替换字符——解码时丢了字节（真出现乱码了）"
    )


def test_installer_language_file_covers_the_wizard_keys():
    """抽查的向导文案键必须存在，且真的是中文。

    键的挑法：只用**用户一定会看到**的那几个（下一步/上一步/取消/
    安装/完成、选目录、准备安装、完成页标题）。不是要「翻译得完整」，
    而是这几条一旦回退成英文，用户一眼就看出向导没汉化。

    纯占位符格式串（``%1 KB``、``%1 (%2)``）不算漏——那种东西译了
    反而更糟，所以抽查里也不含它们。
    """
    text = ISL.read_text(encoding="utf-8-sig")
    messages = _parse_isl_section(text, "Messages")
    assert messages, "翻译文件里没有 [Messages] 段的条目"

    spot_checks = (
        "ButtonNext", "ButtonBack", "ButtonCancel", "ButtonInstall", "ButtonFinish",
        "WizardSelectDir", "WizardReady", "FinishedHeadingLabel", "FinishedLabel",
    )
    missing = [k for k in spot_checks if k not in messages]
    assert not missing, f"翻译文件缺这些向导文案键：{', '.join(missing)}"

    english = [k for k in spot_checks if not _has_cjk(messages[k])]
    assert not english, (
        "这些向导文案还是英文："
        + "，".join(f"{k}={messages[k]!r}" for k in english)
    )

    # LanguageID 得是简体中文：文件里全是中文但LanguageID 写着繁体，
    # 用户看到的是繁体字——同样是「不是中文」。
    lang_options = _parse_isl_section(text, "LangOptions")
    assert lang_options.get("LanguageID", "").lower().replace(" ", "") in (
        "$0804", "$804",
    ), f"LanguageID 不是简体中文：{lang_options.get('LanguageID')!r}"


def test_installer_language_keys_match_the_default_english_set():
    """翻译的键集不该与Inno Setup 的默认英文集脱节太多。

    少键不是立刻致命（Inno Setup 会退回英文那条），但意味着向导上
    会**零星**冒出英文，比全英文更难看——用户会以为装坏了。

    上限放在「不许超过5%」，是刻意留的余量：官方翻译与 Default.isl
    各自独立演进，某个 Inno Setup 小版本加几条键是正常的。同时把
    当前实测值写进注释，好让下一个人知道基线是多少。

    实测（落库时）：中文翻译 281 键、Default.isl 281 键，**完全对齐**
    （零缺失、零多余）。所以这条守卫当下是「零缺口通过」——一旦变红
    就是真的脱节了，不是「本来就有一点缺」。
    """
    isl_keys = set(_parse_isl_section(
        ISL.read_text(encoding="utf-8-sig"), "Messages"))
    default_path = ROOT / "installer_languages" / "Default.isl"
    if not default_path.is_file():
        pytest.skip(
            "installer_languages/Default.isl 不见了——它只为键集比对而落库，"
            "见该文件头部的说明。拿当前 Inno Setup 版本对应的 Default.isl "
            "放回去即可（要带 UTF-8 BOM）"
        )
    default_keys = set(_parse_isl_section(
        default_path.read_text(encoding="utf-8-sig"), "Messages"))
    assert default_keys, "Default.isl 里没有 [Messages] 条目"

    missing = default_keys - isl_keys
    ratio = len(missing) / len(default_keys)
    assert ratio <= 0.05, (
        f"翻译缺 {len(missing)}/{len(default_keys)} 个键"
        f"（{ratio:.1%} > 5%）：{sorted(missing)[:10]}"
        "——这些位置会显示英文"
    )


def test_ci_proves_the_built_installer_is_chinese():
    """两个 workflow 都必须真的扫一遍编译产物里的中文。

    ``installer.iss``、翻译文件、检查器全对，只说明「配置写对了」，
    说明不了「编出来是这个样子」——静默安装全程不渲染界面，谁也没看过
    那个向导一眼。只有扫产物里的消息文本才算端到端。

    两个 workflow 都要有：``build-installer.yml`` 是改安装相关时的
    验证，``release.yml`` 是真正发出去的那次——只加前者的话，
    「改了没触发 build workflow」时会一路发出去。

    顺带把「断言用的文案确实来自翻译文件」也钉住：CI 里那几句中文是
    手抄的，改了翻译就会对不上，而对不上的表现是 CI 报
    「找不到中文文案」——报的是**安装器坏了**，而真正的原因是断言过时了，
    排查方向会被带偏。
    """
    zh_keys = _parse_isl_section(ISL.read_text(encoding="utf-8-sig"), "Messages")
    # 断言文案住在 tools/verify_installer_zh.ps1 里（内联在两个 workflow
    # 各一份的话，改一边忘了另一边就会漂移），所以校验文案的来源时
    # 要连脚本一起看。
    script = VERIFY_ZH_PS1.read_text(encoding="utf-8")
    assert "选择目标位置" in script, (
        "verify_installer_zh.ps1 没有校验安装器里的中文向导文案——"
        "扫编译产物是唯一能证明 MessagesFile 生效的手段"
    )
    assert "Select Destination Directory" in script, (
        "verify_installer_zh.ps1 缺反向断言：出现英文向导文案才说明 MessagesFile 失效"
    )

    # CI 里断言的每句中文都必须真能在翻译文件里找到
    for phrase in _asserted_phrases(script):
        assert any(phrase in v for v in zh_keys.values()), (
            f"verify_installer_zh.ps1 断言了「{phrase}」，但翻译文件里没有这句——"
            "改了翻译就会让 CI 报「安装器坏了」，而真原因是断言过时了"
        )

    # 两个 workflow 都得真的调这个脚本，且传的是 dist\ 下**刚编出来**
    # 的那份产物。
    for path in (INSTALLER_YML, RELEASE_YML):
        body = path.read_text(encoding="utf-8")
        assert "verify_installer_zh.ps1" in body, (
            f"{path.name} 没有调用 tools/verify_installer_zh.ps1——"
            "只在一个 workflow 里验证的话，「改了没触发另一个」时照样发出去"
        )
        # 只认真正传给脚本的那个路径。注意这里是**行内**匹配而不是全文
        # 搜文件名：上传 artifact 那步也写着 dist\bilibili-submit-setup.exe，
        # 全文搜会把它当成「校验脚本扫的是产物」，而实际上传的是目录。
        assert re.search(
            r"run:[^\n]*verify_installer_zh\.ps1[^\n]*dist\\+bilibili-submit-setup\.exe",
            body,
        ), (
            f"{path.name} 没把 dist\\ 下刚编出来的产物传给校验脚本——"
            "扫错文件等于没扫（比如扫成安装步骤里拷去中立目录的那份）"
        )


def test_zh_verifier_searches_bytes_rather_than_decoding():
    """校验脚本必须做**字节级**子序列搜索，不能整流解码再 ``Contains``。

    这不是风格偏好，是正确性：``[System.Text.Encoding]::Unicode
    .GetString($bytes)`` 只在偏移 0 对齐时才解得出正确字符，而 setup.exe
    里 UTF-16LE 文本的起始偏移是任意的（PE 资源段的对齐决定的）。
    落在奇数偏移时解出来是逐字错位的乱码，``Contains`` 永远匹配不上。

    CI 上真踩过：自证锚点是纯 ASCII 的程序名 ``bilibili-submit``，
    它在字节里明明存在，只因整流解码错位，脚本第一步就抛了
    「连自己的文件名都找不到」——而这句话把方向带偏到「产物不对」，
    真原因（解码方式）是这段代码自己的毛病。
    """
    script = VERIFY_ZH_PS1.read_text(encoding="utf-8")

    assert "Encoding]::Unicode.GetString" not in script, (
        "校验脚本里出现了整流解码（Encoding]::Unicode.GetString）——"
        "偏移不对齐时解出来是乱码，Contains 必然匹配不上；改用字节搜索"
    )
    assert "Test-ByteSubsequence" in script, (
        "校验脚本没有字节级子序列搜索——与偏移无关，是这里唯一可靠的读法"
    )
    # 自证锚点：坏尺子量东西，比不量更糟。
    # 判据是**真的会因为锚点全灭而抛错**，不是「脚本里出现过 anchors
    # 这个词」——把 `if (-not $anchorHit)` 改成 `if ($false)` 就能
    # 让一个只查关键词的守卫变绿，而那正是「尺子坏了却当量到了」
    # 的改法。逐行找那个 throw 才抓得住。
    anchor_lines = script.splitlines()
    branch = [
        i for i, line in enumerate(anchor_lines)
        if "$anchorHit" in line and line.strip().startswith("if")
    ]
    assert branch, "校验脚本里找不到检查 $anchorHit 的分支"
    idx = branch[0]
    assert "not" in anchor_lines[idx], (
        "锚点检查被短路了（没有 not）——自证锚点失效时脚本会静默"
        "得出「没找到中文」的结论，把排查方向带偏到产物上"
    )
    # throw 必须在**这个分支里面**（下一个同缩进的非空行之前）。
    # 只查「脚本里有没有 throw」太松：脚本里有三处 throw，
    # 把锚点分支整个删掉都照样绿。
    tail = []
    body_indent = " " * (len(anchor_lines[idx]) - len(anchor_lines[idx].lstrip()) + 1)
    for line in anchor_lines[idx + 1:]:
        if line.strip() and not line.startswith(body_indent):
            break
        tail.append(line)
    assert any("throw" in line for line in tail), (
        "锚点全灭的分支里没有 throw——脚本会继续往下跑，"
        "拿着失效的尺子把「没找到中文」当成结论报出去"
    )


def _asserted_phrases(script_body: str) -> list[str]:
    """从校验脚本里取出 ``$mustHave = @(...)`` 那几条中文。

    只认数组字面量的内容，不扫全文里所有中文——否则注释里解释
    「为什么不能带引号」的中文也会被当成断言。
    """
    match = re.search(r"\$mustHave\s*=\s*@\((.*?)\)", script_body, re.DOTALL)
    assert match, "校验脚本里找不到 $mustHave 数组"
    return re.findall(r"'([^']+)'", match.group(1))


#: PowerShell 里的字符串定界符只有 ASCII 的单/双引号。
#: 中文文案里的全角引号「」『』不是定界符——写进双引号字符串里，
#: PowerShell 仍会把后续内容当成代码，于是整段脚本 ParserError。
#: CI 上真踩过一次：$mustHave 里放了一句带「」的文案，
#: 整个 step 在第 31 行语法报错，前面 30 行一行都没跑到。
_FULLWIDTH_QUOTES = "“”‘’"


def test_ci_chinese_assertions_survive_powershell_parsing():
    """CI 里断言用的中文不能含全角引号，否则整个 step 语法报错。

    这条不是假设：上一版断言里写了 ``点击“下一步”继续``，CI 上直接
    ``ParserError: Unexpected token '下一步”继续'``——报错行号指向那句
    中文，而看的人只会觉得「脚本怎么有语法问题」，不会立刻想到是
    中文文案里的全角引号。

    同时把「断言文案里没有 ASCII 双引号」一起钉住：那会让
    ``"..."`` 提前闭合，症状一样但更隐蔽。
    """
    script = VERIFY_ZH_PS1.read_text(encoding="utf-8")
    for phrase in _asserted_phrases(script):
        bad = [c for c in _FULLWIDTH_QUOTES if c in phrase]
        assert not bad, (
            f"verify_installer_zh.ps1 的断言文案「{phrase}」含全角引号 {bad}——"
            "PowerShell 不把它当字符串定界符，会让整个 step 语法报错"
        )
        assert '"' not in phrase and "'" not in phrase, (
            f"verify_installer_zh.ps1 的断言文案「{phrase}」含 ASCII 引号，"
            "会提前闭合字符串字面量"
        )


def test_ci_chinese_assertions_avoid_placeholders():
    """断言文案不能含 ``[name]`` 这类编译期占位符。

    编译时它们会被替换成实际应用名（本项目是「哔哩哔哩自动投稿程序」），
    拿带占位符的**原文**去 ``Contains`` 永远匹配不上——CI 会报
    「找不到中文文案」，而真正的原因是断言写错了，不是安装器坏了。
    排查方向会被直接带偏。
    """
    script = VERIFY_ZH_PS1.read_text(encoding="utf-8")
    for phrase in _asserted_phrases(script):
        assert "[" not in phrase and "]" not in phrase, (
            f"verify_installer_zh.ps1 的断言文案「{phrase}」含占位符方括号——"
            "编译时会被替换掉，拿原文匹配不上；取占位符之外的那一段"
        )


def _parse_isl_section(text: str, section: str) -> dict[str, str]:
    """从 .isl 文本里抽出某段的 ``键=值``。

    与 :func:`_section` 一样按独占一行切段（理由同那里），多一步是
    **剔掉注释行**：``.isl`` 里``[LangOptions]`` 上方全是注释说明，
    不剔掉就会把注释内容当成「用户写的值」。
    """
    body = _section(text, section)
    pairs: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        item = re.match(r"^([A-Za-z]\w*)\s*=\s*(.*)$", line)
        if item:
            pairs[item.group(1)] = item.group(2).strip()
    return pairs


def _has_cjk(text: str) -> bool:
    """有没有中日韩统一表意文字。"""
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def test_ci_installs_setup_from_a_neutral_directory():
    """CI 必须把安装器拷到别处再装，不能就地从 ``dist\\`` 跑。

    ``{src}`` 是**安装器 exe 所在的目录**。在 ``dist\\`` 下跑安装，
    ``{src}`` 恰好等于产物目录，任何依赖它的代码都「碰巧正确」；而用户
    是从「下载」文件夹双击的，``{src}`` 是下载目录。

    这两者的差别不是理论问题：曾经有一版安装器在 ``InitializeSetup``
    里拼 ``{src}`` 找打包产物，找不到就中止安装——CI 一路绿，每个下载
    者都装不上，整整一个版本都没人发现。把安装器拷到 ``RUNNER_TEMP``
    下再装，这类 bug 才会在 CI 上现形。
    """
    for path in (INSTALLER_YML, RELEASE_YML):
        body = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        )
        # ① 真的把安装器拷出去了（源在 dist\ 里，目标不在）
        destinations = re.findall(
            r"Copy-Item\s+\"?[^\s\"]*bilibili-submit-setup\.exe\"?\s+(\S+)",
            body,
        )
        assert destinations, (
            f"{path.name} 就地跑 dist\\bilibili-submit-setup.exe——{{src}} 会"
            "恰好等于产物目录，依赖 {src} 的代码在 CI 上永远通过、在用户"
            "机器上永远失败。先 Copy-Item 到别的目录再装"
        )
        assert not any("dist" in dest.lower() for dest in destinations), (
            f"{path.name} 把安装器拷回了 dist\\ 下（{destinations}）——等于没拷"
        )
        # ② 真正交给 Start-Process 的路径也不在 dist\ 里
        assigned = re.findall(r"\$setup\s*=\s*(.+)$", body, re.MULTILINE)
        assert assigned, f"{path.name} 里找不到 $setup 的赋值"
        assert not any("dist" in value.lower() for value in assigned), (
            f"{path.name} 的 $setup 仍指向 dist\\（{assigned}）——{{src}} 会"
            "等于产物目录，测不出用户机器上的失败"
        )


def test_artifacts_are_checked_at_build_time_not_install_time():
    """产物齐不齐全要在**构建期**查，不能放进安装器里查。

    installer.iss 曾经有个 ``[Code]`` 段，在 ``InitializeSetup()`` 里用
    ``{src}`` 拼出产物目录找主程序，找不到就 ``Result := False`` 中止
    安装。本意是「别让产物不全的安装器流出去」，但 ``{src}`` 是
    **安装器 exe 所在的目录**：

    - CI 上：``setup.exe`` 就在 ``dist\\`` 下，拼出来正好是产物目录 → 通过
    - 用户机器上：``setup.exe`` 在「下载」文件夹里，于是去找
      ``下载\\bilibili-submit-gui\\bilibili-submit-gui.exe`` —— 当然没有
      → 弹框「找不到打包产物」并中止

    于是 CI 永远绿、每个下载者都装不上，而这句报错对下载者毫无意义
    （他是来装程序的人，不是打包的人）。

    顺带一提，``{src}`` 和 ``{#BuildDir}`` 还不能直接拼：``[Files]`` 的
    Source 基准是 ``.iss`` 所在目录（仓库根），``{src}`` 的基准是
    ``dist\\``，拼出来是 ``dist\\dist\\bilibili-submit-gui\\``。两个坑
    长在同一行代码里。

    所以那段代码整个删了，校验挪到 ``tools/check_installer.py``——那里
    才有 ``dist\\`` 可看。这条守卫盯两头：安装器里别再冒出运行期的
    文件系统检查；构建期的校验真的接进了两条打包路径，否则它就是个
    没人调用的死脚本。
    """
    text = ISS.read_text(encoding="utf-8-sig")
    code = _section(text, "Code")
    assert "{src}" not in code, (
        "[Code] 段里出现了 {src}——它是**安装器 exe 所在目录**，在用户机器上"
        "是「下载」文件夹而不是 dist\\。拿它拼路径做运行期检查，会让每个"
        "下载者都装不上，而 CI 上永远绿。产物校验属于构建期，"
        "见 tools/check_installer.py"
    )

    checker = (ROOT / "tools" / "check_installer.py").read_text(encoding="utf-8")
    assert "_check_built_artifacts" in checker, (
        "tools/check_installer.py 里应当有产物校验（_check_built_artifacts）——"
        "没有它，产物目录空着也能编出一个「装完双击闪退」的安装器"
    )

    for label, path in (("build_windows.bat", ROOT / "build_windows.bat"),
                        ("release.yml", RELEASE_YML)):
        # 只看非注释行：说明这段坑的注释里就得写 installer.iss 长什么样
        body = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith(("#", "::", "REM", "rem"))
        )
        assert "check_installer.py" in body, (
            f"{label} 编译安装器前没跑 check_installer.py——产物缺主程序时"
            "ISCC 照样编得出来，只是装完闪退"
        )
        assert body.index("check_installer.py") < body.index("installer.iss"), (
            f"{label} 里 check_installer.py 跑在 installer.iss 之后——"
            "校验必须在编译**之前**，编出来再查就晚了"
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

    本文件现在**没有** ``[Code]`` 段（产物校验已迁到构建期），所以真实
    文件那一半是空的。光查真实文件的话这条测试会变成一条谁都红不了的
    空断言——因此先拿一段故意写错的样本证明判据本身有效，再拿它去扫
    真文件：将来有人重新引入 ``[Code]`` 段时它才会真的响。
    """
    def declared_kinds(source: str) -> dict[str, str]:
        return {
            name: kind
            for kind, name in re.findall(
                r"^\s*(function|procedure)\s+(\w+)\s*[\(:;]", source, re.MULTILINE
            )
        }

    # 先证明判据有效：这段里 InitializeWizard 写成 function，必须被认出来
    sample = "\n".join([
        "[Code]",
        "function InitializeSetup(): Boolean;",
        "begin Result := True; end;",
        "function InitializeWizard(): Boolean;",
        "begin Result := True; end;",
    ])
    assert declared_kinds(sample).get("InitializeWizard") == "function", (
        "判据失效：样本里写错的 function InitializeWizard 没被识别出来，"
        "下面对真实文件的检查就只是一条空断言"
    )

    declared = declared_kinds(_section(ISS.read_text(encoding="utf-8-sig"), "Code"))
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


def test_installer_ships_a_user_guide():
    """安装版必须带一份「使用说明」，并且给好两个入口。

    安装目录里**刻意不放** README.md——那是 GitHub 主页的文档
    （徽章、下载表、构建说明），对已经装好软件的人来说全是噪音；
    而且 Windows 上双击 ``.md`` 默认没有打开方式。取而代之的是一份
    面向已安装用户的 ``docs/user-guide.html``（离线可看，双击即开），
    并接好两个入口：

    - 开始菜单里的「使用说明」；
    - 安装完成页的「查看使用说明」勾选项——第一次装好的人最需要的
      不是程序本身，是知道怎么用。

    这条守卫盯三处：入口在 installer.iss 里真的存在；两个打包流程
    （本地 bat 与 CI）都把文件放进产物目录；说明页本身是**自包含**
    的——引用了外部 CSS/JS/图片的话，在用户机器上打开就是残页。
    """
    text = ISS.read_text(encoding="utf-8-sig")

    icons = _directives(text, "Icons")
    assert icons, "[Icons] 段应该有实际指令"
    assert 'Filename: "{app}\\user-guide.html"' in icons, (
        "开始菜单没有「使用说明」入口——安装版里那份说明就没人能发现"
    )

    run_section = _directives(text, "Run")
    assert run_section, "[Run] 段应该有安装完成页的「查看使用说明」勾选项"
    assert "user-guide.html" in run_section and "postinstall" in run_section, (
        "安装完成页缺「查看使用说明」勾选项"
    )
    # HTML 不是可执行文件，必须 shellexec 让 Windows 挑默认浏览器，
    # 否则 Inno 会试图直接执行它然后失败。skipifsilent 是给 CI 的
    # 静默安装准备的——不能在无人值守的机器上弹浏览器。
    for flag in ("shellexec", "skipifsilent"):
        assert flag in run_section, (
            f"[Run] 的 user-guide.html 缺 {flag} 标志——"
            + ("不带 shellexec 时 Inno 会直接执行 .html 然后失败"
               if flag == "shellexec" else
               "不带它，CI 的静默安装会在无人值守的机器上弹浏览器")
        )

    guide = ROOT / "docs" / "user-guide.html"
    assert guide.is_file(), "docs/user-guide.html 不存在"

    # 自包含：不许引用外部资源。相对路径引用在 CI 打包机上能解析，
    # 到用户机器上（只有安装目录、没有 docs/images）就是残页。
    html = guide.read_text(encoding="utf-8")
    externals = re.findall(
        r'(?:<link[^>]+href=|<script[^>]+src=|<img[^>]+src=|@import\s+|url\()'
        r'\s*["\']?(https?://|/|\.\./)[^"\')\s]*',
        html,
    )
    assert not externals, (
        f"user-guide.html 引用了外部资源（{sorted(set(externals))}）——"
        "它是随安装包发给用户离线看的，必须自包含：样式内联、不引图片"
    )
    # 设计语言与 GUI 同源：色值来自 bilibili_submit/ui/theme.py。
    # 改了 GUI 主题却忘了同步这份页面时，这条会提醒。
    assert "#FB7299" in html and "#241A1F" in html, (
        "user-guide.html 里找不到 GUI 主题色（#FB7299 粉 / #241A1F 墨）——"
        "它的配色应当与程序一致（见 bilibili_submit/ui/theme.py）"
    )

    for label, path in (("build_windows.bat", ROOT / "build_windows.bat"),
                        ("release.yml", RELEASE_YML),
                        ("build-installer.yml", INSTALLER_YML)):
        body = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith(("#", "::", "REM", "rem"))
        )
        # 必须出现「从 docs 拷贝」的那一行，光有文件名不算——
        # 冒烟检查数组里也写着 user-guide.html，只搜文件名的话，
        # 删掉拷贝动作它照样绿（自检抓出来的假绿）。
        assert re.search(r"docs[/\\]user-guide\.html", body), (
            f"{label} 没把 docs/user-guide.html 放进产物目录——"
            "安装器 [Files] 按目录打包，产物目录里没有它就装不进去"
        )

    # 反向：安装目录里刻意**不放** README.md。命令行 zip 包（stage-mini/
    # stage-full）里的 README 不受影响，这里只盯 GUI 产物目录这一条。
    bat_body = "\n".join(
        line for line in (ROOT / "build_windows.bat")
        .read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith(("REM", "rem", "::"))
    )
    assert 'README.md "dist\\bilibili-submit-gui' not in bat_body, (
        "build_windows.bat 又在往安装版产物里拷 README.md——"
        "安装目录只放 user-guide.html，README 是 GitHub 主页的文档"
    )
    for path in (RELEASE_YML, INSTALLER_YML):
        body = "\n".join(
            line for line in path.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        )
        assert '"$dir\\README.md"' not in body, (
            f"{path.name} 又在往安装版产物目录拷 README.md——"
            "安装目录只放 user-guide.html"
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


def test_release_notes_survive_powershell_escaping():
    """Release 说明的 here-string 是可展开的（@\"...\"@），反引号会被
    PowerShell 当**转义字符**处理。

    `` `b `` 是退格、`` `f `` 是换页——v0.2.7-rc.2 的 Release 说明里，
    所有 ``bilibili-`` 的首字母 b、``full`` 的 f、``ffmpeg.exe`` 的 f
    都被吃掉了，页面上显示 ``ilibili-submit-setup.exe``。Markdown 行内
    代码要写字面反引号，在 PowerShell 里必须写成两个（展开后还原成一个）。
    """
    release = RELEASE_YML.read_text(encoding="utf-8")
    m = re.search(r'\$notes = @"(.*?)"@', release, re.S)
    assert m, "release.yml 里找不到 notes here-string"

    # 转义发生在 PowerShell 解析的时候：先把「正确的双反引号」剔除，
    # 剩下还挨着转义字符的单反引号才是风险。展开后的文本里 `` `b ``
    # 反而是正常内容（行内代码的开头），不能在那一层查。
    residual = m.group(1).replace("``", "")
    bad = re.findall(r"`[bfntrva0u]", residual)
    assert not bad, (
        f"notes 里还有会被 PowerShell 转义的反引号序列: {bad}——"
        "Markdown 行内代码的反引号在 here-string 里必须写成两个"
    )
    # 再按 PowerShell 的规则展开，验证最终文本里文件名是完整的
    expanded = m.group(1).replace("``", "`")
    for name in (
        "bilibili-submit-setup.exe",
        "bilibili-submit-gui-portable.exe",
        "bilibili-submit-standalone.exe",
        "bilibili-submit.exe",
        "bilibili-submit-full-windows.zip",
        "bilibili-submit-mini-windows.zip",
    ):
        assert name in expanded, f"展开后的 notes 里文件名不完整: {name}"


def _replica_subseq(hay: bytes, needle: bytes) -> bool:
    """Python 复刻 ``Test-ByteSubsequence``：首字节筛候选 + 逐字节比。"""
    if not needle or len(hay) < len(needle):
        return False
    i = hay.find(needle[0])
    while i != -1 and i <= len(hay) - len(needle):
        if hay[i:i + len(needle)] == needle:
            return True
        i = hay.find(needle[0], i + 1)
    return False


def _replica_any_utf(hay: bytes, text: str) -> bool:
    """Python 复刻 ``Test-AnyUtf``：UTF-8 与 UTF-16LE 任一命中即可。"""
    return _replica_subseq(hay, text.encode("utf-8")) or _replica_subseq(
        hay, text.encode("utf-16-le"))


def _fake_setup(messages: list[str]) -> bytes:
    """造一个 UTF-16LE 落在**奇数偏移**的假 setup.exe。

    长度刻意取奇数（MZ 头 8 字节 + 99 字节填充 = 107），
    整流解码必然错位——这正是 CI 上那次失败的场景。
    """
    body = b"MZ" + b"\x90\x00" * 3 + b"\xAB" * 99
    assert len(body) % 2 == 1, "样本必须是奇数长度，否则测不到错位"
    for s in messages:
        body += s.encode("utf-16-le") + b"\x00\x00"
    return body


def test_zh_verifier_finds_messages_at_odd_byte_offsets():
    """字节搜索在 UTF-16LE 落在**奇数偏移**时照样找得到——这正是 CI 上翻车的那次。

    这里用 Python 精确复刻 ``tools/verify_installer_zh.ps1`` 里的
    ``Test-ByteSubsequence`` / ``Test-AnyUtf``（同样的首字节筛选 +
    逐字节比），在两种编造的 ``setup.exe`` 上跑：

    * 中文版：三条 mustHave 全中、mustNot 全不中 → 判定「是中文向导」
    * 英文版：mustHave 全不中、mustNot 命中英文向导页 → 判定「不是」

    为什么不只在 CI 上验：那边只能拿到「绿/红」，红的时候也只有一句
    throw 文案，分不清是搜索逻辑坏了、产物不对、还是安装器真是英文的。
    这里能把三种情况在本地就分开。
    """
    script = VERIFY_ZH_PS1.read_text(encoding="utf-8")
    must_have = _asserted_phrases(script)
    must_not = _asserted_phrases_in(script, "mustNot")
    assert must_have and must_not, "断言文案不能是空的，否则这条测试是空断言"

    zh_exe = _fake_setup(must_have)
    for phrase in must_have:
        assert _replica_any_utf(zh_exe, phrase), f"字节搜索在奇数偏移下漏了「{phrase}」"
    for phrase in must_not:
        assert not _replica_any_utf(zh_exe, phrase), f"中文产物里不该出现「{phrase}」"

    en_exe = _fake_setup(["Select Destination Directory"])
    for phrase in must_have:
        assert not _replica_any_utf(en_exe, phrase), f"英文产物里不该命中「{phrase}」"
    assert _replica_any_utf(en_exe, "Select Destination Directory"), (
        "反向断言失效：英文向导页没被检出，这条检查就只会单向通过"
    )

    # 旧做法的对照：同一份字节，整流解码必然失败
    decoded = zh_exe.decode("utf-16-le", errors="replace")
    assert not any(p in decoded for p in must_have), (
        "样本没能复现错位——CI 上那次失败的前提（奇数偏移）已经不成立了，"
        "这条测试就变成了空断言"
    )


def _asserted_phrases_in(script_body: str, array_name: str) -> list[str]:
    """取出 ``$mustNot = @(...)`` 这类数组里的字符串字面量。"""
    match = re.search(
        r"\$" + array_name + r"\s*=\s*@\((.*?)\)", script_body, re.DOTALL)
    assert match, f"校验脚本里找不到 ${array_name} 数组"
    return re.findall(r"'([^']+)'", match.group(1))
