"""installer.iss 的静态检查。

ISCC 只能在 Windows 上跑，所以这里**不是**替代编译，而是把低级错误
挡在提交之前：段名拼错、指令拼写不对、#define 没定义、每行分号、
大括号不配对、文件路径对不上仓库实际布局、以及安装向导的消息文件
是不是真的中文。

真正的验证仍然是 CI 里Windows runner 上跑 ISCC。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _force_utf8_output() -> None:
    """把 stdout/stderr 强制成 UTF-8。

    本脚本会打印中文（产物目录名、错误说明），而 Windows 上 Python
    默认用控制台代码页：PowerShell 是 cp1252、cmd.exe 是 cp936。
    两者都**编码不了**部分中文，一句 print 就会抛 UnicodeEncodeError
    直接崩掉——CI 上真发生过：脚本在 Linux（UTF-8）跑得好好的，
    到了 Windows runner 第一条 print 就把整个 job 打成 failure。

    一个检查 Windows 安装器的脚本自己死在 Windows 编码上，是这个
    工具最不该有的失败方式。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):  # 非 TextIO / 已被包装
            pass


ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "installer.iss"

#: Inno Setup 的合法段名。写错段名 ISCC 会报"section not found"，
#: 但那是在 CI 上才看得到——这里提前拦。
SECTIONS = {
    "[Setup]", "[Types]", "[Components]", "[Tasks]", "[Files]", "[Dirs]",
    "[Icons]", "[INI]", "[Registry]", "[Run]", "[UninstallRun]",
    "[UninstallDelete]", "[InstallDelete]", "[Languages]", "[CustomMessages]",
    "[Messages]", "[Code]", "[License]", "[InfoBefore]", "[Wizard]",
    "[WizardImages]", "[Components]", "[Output]",
}

#: 仓库里的安装器资源目录（[Languages] 引用的消息文件放在这儿）。
LANG_DIR = "installer_languages"

#: 常见指令（够用了，不追求穷举——目的是抓拼写错误而非完整校验）
DIRECTIVES = {
    "AppId", "AppName", "AppVersion", "AppVerName", "AppPublisher",
    "AppPublisherURL", "AppSupportURL", "AppUpdatesURL", "DefaultDirName",
    "DefaultGroupName", "OutputBaseFilename", "OutputDir", "SetupIconFile",
    "UninstallDisplayIcon", "WizardStyle", "Compression", "SolidCompression",
    "PrivilegesRequired", "PrivilegesRequiredOverridesAllowed", "LicenseText",
    "Name", "Filename", "Description", "Flags", "Tasks", "Types", "Source",
    "DestDir", "MessagesFile", "GroupDescription", "Type", "WorkingDir",
    "Parameters", "Check", "AfterInstall", "BeforeInstall", "Components",
    "MinVersion", "DisableDirPage", "AlwaysRestart", "AppMutex",
    "CloseApplications", "RestartApplications", "SetupLogging",
}

#: Inno Setup 事件函数的原型：名字 → function / procedure。
#: 只列本项目会用到的几个——写得不全不会误报（查不到就放行），
#: 写错了才会报。新增 [Code] 事件时顺手补进来。
EVENT_PROTOTYPES = {
    "InitializeSetup": "function",     # 返回 Boolean，False 可拒绝安装
    "InitializeWizard": "procedure",   # 只有初始化，没有返回值
    "DeinitializeSetup": "procedure",
    "CurStepChanged": "procedure",
    "CurPageChanged": "procedure",
    "NextButtonClick": "function",
    "BackButtonClick": "function",
    "ShouldSkipPage": "function",
    "PrepareToInstall": "function",
    "CheckPassword": "function",
}

errors: list[str] = []
warnings: list[str] = []


def fail(message: str) -> None:
    errors.append(message)


def warn(message: str) -> None:
    warnings.append(message)


def _check_encoding(raw: bytes) -> None:
    """Inno Setup 6 靠 BOM 认出 UTF-8。

    存成不带 BOM 的 UTF-8 或 GBK，中文会在安装向导里变成乱码，
    **而且不报错**——只在用户眼前发生。所以这一条要在提交前拦住。
    """
    if not raw.startswith(b"\xef\xbb\xbf"):
        fail("installer.iss 必须存为 UTF-8 with BOM，否则中文会乱码")


def _check_sections(lines: list[str]) -> set[str]:
    """段名拼错在 ISCC 上才报得到，这里提前拦。

    ``\\r`` 一并吃掉：.gitattributes 把 installer.iss 定成 ``eol=crlf``，
    Windows 上检出就是 CRLF，不吃 \\r 会把每个段名都当成不合法。
    """
    seen: set[str] = set()
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and "=" not in stripped:
            name = stripped
            if name not in SECTIONS:
                fail(f"第 {number} 行：段名 {name} 不存在（是不是拼错了？）")
            if name in seen:
                # [Components] 之类不会重复；重复通常意味着合并时忘了删
                fail(f"第 {number} 行：段 {name} 出现了两次")
            seen.add(name)
    return seen


def _check_defines(text: str, defines: dict[str, str]) -> None:
    """引用了未定义的 #define 时 ISCC 才报错。"""
    for match in re.finditer(r"\{#(\w+)\}", text):
        name = match.group(1)
        if name not in defines:
            line_no = text[: match.start()].count("\n") + 1
            fail(f"第 {line_no} 行：引用了未定义的 #define {name}")


def _check_braces(text: str) -> None:
    """花括号配对。

    先剥掉两类**不是配对括号**的写法，否则误报：
      * ``{{GUID}}`` —— 两个 {{ 表示字面量 {（AppId 必须这么写）；
      * ``{app}`` / ``{src}`` / ``{code:...}`` 等常量与预处理指令。
    真正要防的是「用户写了个 { 常量} 却漏了 }」这种手误。
    """
    scrubbed = re.sub(r"\{\{", "\x00", text)
    scrubbed = re.sub(r"\x00[^{}]*\}", "\x00", scrubbed)
    scrubbed = re.sub(r"\{[^{}]*\}", "", scrubbed)
    depth = 0
    for match in re.finditer(r"\{|\}", scrubbed):
        depth += 1 if match.group() == "{" else -1
        if depth < 0:
            line_no = scrubbed[: match.start()].count("\n") + 1
            fail(f"第 {line_no} 行：多余的 }}")
            return
    if depth > 0:
        fail(f"有 {depth} 个 {{ 没有对应的 }}")


def _check_directives(lines: list[str]) -> None:
    """指令拼写。``[Code]`` 段里是 Pascal，不是 Key=Value，跳过。"""
    in_code = False
    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and "=" not in stripped:
            in_code = stripped == "[Code]"
            continue
        if in_code or not stripped or stripped.startswith((";", "#", "{")):
            continue
        match = re.match(r"^([A-Za-z]\w*)\s*[=:]", stripped)
        if not match:
            continue
        key = match.group(1)
        if key not in DIRECTIVES:
            warn(
                f"第 {number} 行：{key} 不在已知指令表里"
                "（若确实拼错了请补进脚本顶部的 DIRECTIVES）"
            )


def _check_code_prototypes(text: str) -> None:
    """``[Code]`` 段里事件函数的原型对不对。

    这类错误**只有真跑 ISCC 才看得到**（本项目 CI 上真踩过：
    ``InitializeWizard`` 写成了 ``function ... : Boolean``，ISCC 报
    ``Invalid prototype for 'InitializeWizard'``，而 installer.iss
    的其他部分一点问题都没有）。ISCC 只能跑在 Windows 上，所以能在
    这里挡住就省一整轮 CI。

    只查表里有的名字：不在表里的自定义函数照样放行，免得误报。

    注意 Inno Setup 里这两个长得像但不一样——
    ``InitializeSetup`` 是 function（返回值能拒绝安装），
    ``InitializeWizard`` 是 procedure（只做初始化，没有返回值）。
    """
    code = _section(text, "Code")
    if not code:
        return
    for line_no, line in enumerate(code.splitlines(), 1):
        # CRLF 下 splitlines 已经吃掉 \r，协议行的正则不必管它；
        # 但注释里可能出现 ^ 行尾字符，故 strip 一下再匹配。
        stripped = line.strip()
        match = re.match(r"^(function|procedure)\s+(\w+)\s*(\([^)]*\))?\s*(:\s*\w+)?\s*;", stripped)
        if not match:
            continue
        kind, name = match.group(1), match.group(2)
        expect = EVENT_PROTOTYPES.get(name)
        if expect is None:
            continue  # 自定义函数，不查
        if kind != expect:
            fail(
                f"[Code] 第 {line_no} 行：{name} 应该是 {expect} 而不是 "
                f"{kind}——ISCC 会报 Invalid prototype for '{name}'"
            )


def _section(text: str, name: str) -> str:
    """按 ``[Name]`` **独占一行**切出段内容。

    不能用 ``text.split(name)[-1].split("\\n[")[0]``：注释里随手写一句
    「忘了 [Files] 就会装坏」就会把段名混进搜索结果，切出来的却是
    文件最后一段。本项目真踩过——[Code] 段的注释里提了一句 [Files]，
    于是 recursesubdirs 检查跑去 [Code] 里找，必然报「缺 recursesubdirs」。

    **行尾必须吃 ``\\r``**：``[ \\t]*$`` 在 CRLF 文件上永远不成立
    （``$`` 只在 ``\\n`` 前匹配，而 ``\\r`` 夹在中间），段会「找不到」——
    ``_section`` 返回空串，调用方把「段不存在」当成「段里没有违规内容」
    就放行了。.gitattributes 里 installer.iss 与
    ``installer_languages/*.isl`` 都写了 ``eol=crlf``，Windows 上检出
    就是 CRLF，所以这不是假设而是必然。

    实测：当前这份 installer.iss 即使段切分完全失效，输出依然完整、
    退出码依然是 0——因为它的段首尾都挨着别的段，尾部锚点坏掉不影响
    段头匹配。所以这里防的是「段名后带空格」或「段在文件末尾」的那类
    改法。守卫在 tests/test_packaging.py 的
    test_checker_also_survives_crlf_line_endings。
    """
    pattern = re.compile(
        rf"^\[{re.escape(name)}\][ \t\r]*$", re.MULTILINE
    )
    match = pattern.search(text)
    if not match:
        return ""
    rest = text[match.end():]
    nxt = re.search(r"^\[[^\]]+\][ \t\r]*$", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def _check_build_layout(text: str, defines: dict[str, str]) -> None:
    """``BuildDir`` / ``AppExeName`` 与 spec 的输出对不对得上。

    真正的编译验证在 CI（ISCC 只能跑在 Windows 上）。这里能查的是
    「名字对不上」——那类错误 ISCC 不报，只会装出一个缺文件的安装器。
    """
    build_dir = defines.get("BuildDir", "").strip().strip('"')
    exe_name = defines.get("AppExeName", "").strip().strip('"')
    if not build_dir or not exe_name:
        fail("BuildDir 或 AppExeName 没定义")
        return
    dir_name = Path(build_dir.replace("\\", "/")).name
    stem = Path(exe_name).stem
    if dir_name != stem:
        fail(
            f"BuildDir 末段 {dir_name} 与 AppExeName 主名 {stem} 不一致——"
            "PyInstaller 的 EXE_NAME 必须同时对上这两处"
        )
    print(f"  产物目录: dist\\{dir_name}\\")
    print(f"  主程序  : {exe_name}")
    # 产物齐不齐全只能在这里（构建期）查——安装器里查不了：{src} 在用户
    # 机器上是「下载」文件夹，不是 dist\。详见 installer.iss 末尾的说明。
    _check_built_artifacts(dir_name, exe_name)


def _check_built_artifacts(dir_name: str, exe_name: str) -> None:
    r"""产物目录里的主程序与运行时在不在。

    ISCC 不检查 [Files] 的通配路径，产物目录空着它也照样编出一个
    「装完双击闪退」的安装器。所以这一步必须在编译**之前**跑。

    只有 ``dist\`` 存在时才查——从没打包过的机器上不该因为缺产物而失败。
    """
    dist_dir = ROOT / "dist" / dir_name
    if not dist_dir.exists():
        print(f"  （dist\\{dir_name}\\ 不存在，跳过产物校验——请先打包）")
        return

    exe = dist_dir / exe_name
    if not exe.exists():
        fail(
            f"产物里没有主程序：{exe}——现在编译会得到一个装完闪退的安装器。"
            "先跑 build_windows.bat（或 CI 的打包步骤）"
        )
        return
    # _internal 是 PyInstaller 的运行时目录，少了 exe 起不来
    if not (dist_dir / "_internal").exists():
        fail(f"产物里缺 _internal\\（Python 运行时）：{dist_dir}")
    # ffmpeg 缺了只影响自动抽帧，程序本身能跑，所以是警告不是失败
    if not (dist_dir / "ffmpeg.exe").exists():
        warn("产物里没有 ffmpeg.exe，自动抽帧（cover: auto）将不可用")
    print("  产物校验: 主程序与 _internal 齐全")


def _check_files_section(text: str) -> None:
    """``recursesubdirs`` 缺失与 AppId 缺失。"""
    files_section = _section(text, "Files")
    if files_section:
        # 只看指令行：注释里把「recursesubdirs 一定要开」写了一遍，
        # 在整段文本里搜索的话，删掉真指令也照样搜得到。
        directives = [
            line for line in files_section.splitlines()
            if line.strip() and not line.strip().startswith(";")
        ]
        if not any("recursesubdirs" in line for line in directives):
            fail(
                "[Files] 缺 recursesubdirs——_internal\\ 下的 Python 运行时"
                "装不进去，程序会双击闪退"
            )
    if not re.search(r"^AppId\s*=", text, re.MULTILINE):
        fail("缺 AppId：没有它 Inno Setup 认不出是同一个程序，升级会变成装两份")


def _check_icon(defines: dict[str, str]) -> None:
    """图标文件必须存在，否则 ISCC 报 "icon file not found"。"""
    icon = defines.get("SetupIconFile", "")
    if not icon:
        return
    if not (ROOT / icon.replace("\\", "/")).is_file():
        fail(f"SetupIconFile 指向的文件不存在：{icon}")


#: 装向导上用户一定会看到的那几个键。列在这里不是因为「翻译要全」，
#: 而是这些键一旦回退成英文，用户立刻能看出向导没汉化。
#: （实测这份官方中文翻译：281 个 [Messages] 键里只有 5 条不含中文，
#:   其中 4 条是 "%1 KB"、"%1 (%2)" 这种纯占位符格式，本来就不该译。）
_WIZARD_SPOT_CHECKS = (
    "ButtonNext", "ButtonBack", "ButtonCancel", "ButtonInstall", "ButtonFinish",
    "WizardSelectDir", "WizardReady", "FinishedHeadingLabel", "FinishedLabel",
)


def _parse_section_keys(text: str, section: str) -> dict[str, str]:
    """切出某段里的 ``键=值``，键名照抄（不规范化大小写）。

    :func:`_section` 按独占一行切段；这里再把 ``;`` 开头的注释行
    剔掉，免得把注释内容当成「用户写的值」报出去（报 ``LanguageID
    不是 $0804，实际是 "; The following three entries..."`` 这种
    话很显然是没读懂的报错）。行尾吃掉 ``\\r``，理由同 :func:`_section`。
    """
    match = re.search(rf"^\[{re.escape(section)}\][ \t\r]*$", text, re.MULTILINE)
    if not match:
        return {}
    rest = text[match.end():]
    nxt = re.search(r"^\[[^\]]+\][ \t\r]*$", rest, re.MULTILINE)
    body = rest[: nxt.start()] if nxt else rest
    pairs: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(";"):
            continue
        item = re.match(r"^([A-Za-z]\w*)\s*=\s*(.*)$", line)
        if item:
            pairs[item.group(1)] = item.group(2).strip()
    return pairs


def _parse_messages(isl_text: str) -> dict[str, str]:
    """从 .isl 文本里抽出 ``[Messages]`` 的键值对。"""
    return _parse_section_keys(isl_text, "Messages")


def _parse_lang_options(isl_text: str) -> dict[str, str]:
    """从 .isl 文本里抽出 ``[LangOptions]`` 的键值对。"""
    return _parse_section_keys(isl_text, "LangOptions")


def _check_languages(text: str) -> None:
    """``[Languages]`` 指向的消息文件得存在、得是中文、编码得对。

    这一段是被一个真实 bug 逼出来的：那时的 [Languages] 写着
    ``Name: "chinese"; MessagesFile: "compiler:Default.isl"``——
    语言名叫 chinese（所以「语言」这一栏看着是对的），
    消息文件却是**英文**的 Default.isl，装出来整个向导全是英文，
    而仓库里没有任何一个检查能发现「向导不是中文的」。

    ISCC 对这两种错法的态度都是沉默的：不存在的路径在部分环境下
    会被 fallback 到默认语言，Default.isl 更是合法得不能更合法。
    所以只能自己查。
    """
    languages = _section(text, "Languages")
    if not languages:
        fail("缺 [Languages] 段：安装向导会退回英文")
        return

    entries = re.findall(
        r'^\s*Name\s*:\s*"([^"]*)"\s*;\s*MessagesFile\s*:\s*"([^"]*)"',
        languages,
        re.MULTILINE,
    )
    if not entries:
        fail("[Languages] 里没有成对的 Name/MessagesFile 指令")
        return

    for name, messages_file in entries:
        # 路径里可能带 compiler: 前缀，取 basename 前要先剥掉——
        # 否则 "compiler:Default.isl" 的 basename 还是整串，
        # 下面的英文判断就永远不成立（这个 bug 原本就是
        # compiler:Default.isl，所以它必须是能被抓住的那一个）。
        bare = messages_file.lower().split(":", 1)[-1]
        if Path(bare.replace("\\", "/")).name == "default.isl":
            fail(
                f"[Languages] 的 {name!r} 指向 Default.isl（英文）——"
                "装出来会是英文向导。指向 installer_languages\\ 下的中文翻译"
            )
            continue
        # 官方写法 compiler:xxx.isl 会去 ISCC 安装目录里找；本项目刻意
        # vendored 到仓库里，好处就是能对着它做检查（编码、键集、内容）。
        if messages_file.lower().startswith("compiler:"):
            fail(
                f"[Languages] 的 {name!r} 指向 compiler: 前缀"
                "（依赖构建机上 Inno Setup 的安装布局）；"
                f"改用仓库内的 {LANG_DIR}\\ChineseSimplified.isl"
            )
            continue
        _check_isl_file(messages_file, name)


def _check_isl_file(relative: str, language_name: str) -> None:
    """消息文件本身：存在、UTF-8 带 BOM、有 LanguageID、真的是中文。"""
    path = ROOT / relative.replace("\\", "/")
    if not path.is_file():
        fail(f"[Languages] 的 {language_name!r} 指向的文件不存在：{relative}")
        return

    raw = path.read_bytes()
    if not raw.startswith(b"\xef\xbb\xbf"):
        fail(
            f"{relative} 必须存为 UTF-8 with BOM，否则 ISCC 按 ANSI 读，"
            "中文会乱码而且不报错"
        )

    text = raw.decode("utf-8-sig", errors="replace")
    lang_options = _parse_lang_options(text)
    # 只问 LanguageID：写成 $804 也算对。判据不能连带要求
    # LanguageName 含中文——LanguageName 只是语言下拉框里的显示名，
    # 而本项目只有一种语言，Inno Setup 压根不显示那个下拉框。
    # 它的唯一用户是「有人把 LanguageID 配错了」的时候。
    language_id = lang_options.get("LanguageID", "")
    if language_id.lower().replace(" ", "") not in ("$0804", "$804"):
        fail(
            f"{relative} 的 LanguageID 不是 $0804（简体中文），"
            f"实际是 {language_id or '（没写）'}"
        )

    messages = _parse_messages(text)
    if not messages:
        fail(f"{relative} 里没有 [Messages] 段的消息条目")
        return
    missing = [k for k in _WIZARD_SPOT_CHECKS if k not in messages]
    if missing:
        fail(f"{relative} 缺这些向导文案键：{', '.join(missing)}")
        return

    # 抽查的键里只要有没中文的，用户就能一眼看出向导没汉化。
    english = [k for k in _WIZARD_SPOT_CHECKS if not _has_cjk(messages[k])]
    if english:
        detail = "，".join(f"{k}={messages[k]!r}" for k in english)
        fail(f"{relative} 里这些向导文案还是英文：{detail}")

    untranslated = sum(
        1 for v in messages.values() if not _has_cjk(v) and re.search(r"%\d", v)
    )
    print(
        f"  语言 {language_name}: {relative}"
        f"（{len(messages)} 条消息，含占位符的纯格式串 {untranslated} 条不译）"
    )


def _has_cjk(text: str) -> bool:
    """有没有中日韩统一表意文字。"""
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def main() -> int:
    _force_utf8_output()
    if not ISS.is_file():
        print(f"找不到 {ISS}")
        return 1

    raw = ISS.read_bytes()
    _check_encoding(raw)
    text = raw.decode("utf-8-sig")
    lines = text.splitlines()
    if not lines:
        print("installer.iss 是空的")
        return 1

    _check_sections(lines)
    defines = {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"^#define\s+(\w+)\s+(.*)$", text, re.MULTILINE)
    }
    _check_defines(text, defines)
    _check_braces(text)
    _check_directives(lines)
    _check_build_layout(text, defines)
    _check_files_section(text)
    _check_code_prototypes(text)
    _check_icon(defines)
    _check_languages(text)

    for message in warnings:
        print(f"[warn] {message}")
    for message in errors:
        print(f"[FAIL] {message}")
    if errors:
        print(f"\n{len(errors)} 个错误")
        return 1
    print(f"\ninstaller.iss 静态检查通过（{len(lines)} 行，{len(defines)} 个 #define）")
    print("注意：这只排低级错误，真正的编译验证在 CI 的 Windows runner 上")
    return 0


if __name__ == "__main__":
    sys.exit(main())
