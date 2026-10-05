"""installer.iss 的静态检查。

ISCC 只能在 Windows 上跑，所以这里**不是**替代编译，而是把低级错误
挡在提交之前：段名拼错、指令拼写不对、#define 没定义、每行分号、
大括号不配对、文件路径对不上仓库实际布局。

真正的验证仍然是 CI 里Windows runner 上跑 ISCC。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

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
    """段名拼错在 ISCC 上才报得到，这里提前拦。"""
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
    if f"{dir_name}\\{exe_name}" not in text:
        warn(f"脚本里没有出现 {dir_name}\\{exe_name}，确认路径拼接正确")


def _check_files_section(text: str) -> None:
    """``recursesubdirs`` 缺失与 AppId 缺失。"""
    if "[Files]" in text:
        files_section = text.split("[Files]")[-1].split("\n[")[0]
        if "recursesubdirs" not in files_section:
            fail(
                "[Files] 缺 recursesubdirs——_internal\\ 下的 Python 运行时"
                "装不进去，程序会双击闪退"
            )
    if "AppId" not in text:
        fail("缺 AppId：没有它 Inno Setup 认不出是同一个程序，升级会变成装两份")


def _check_icon(defines: dict[str, str]) -> None:
    """图标文件必须存在，否则 ISCC 报 "icon file not found"。"""
    icon = defines.get("SetupIconFile", "")
    if not icon:
        return
    if not (ROOT / icon.replace("\\", "/")).is_file():
        fail(f"SetupIconFile 指向的文件不存在：{icon}")


def main() -> int:
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
    _check_icon(defines)

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
