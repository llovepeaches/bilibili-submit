"""bili_submit.spec 在 Linux 下的行为测试。

与 ``test_spec_bundle.py`` 的分工：那边钉的是**与平台无关**的约定
（GUI 带 tkinter、ffmpeg 内嵌、EXE_NAME 可覆盖），这里钉的是平台
分叉本身——同一份 spec 现在要同时产出 Windows exe 和 Linux AppImage，
「Windows 行为逐字不变」是这个改动能合进来的前提，所以两边都得测。

为什么必须靠测试而不是靠人记住：spec 不是普通模块，改错的表现不是
单元测试变红，而是**构建机上一句看不懂的报错**——比如 Linux 上传
``icon=xxx.ico``，PyInstaller 会去读一个 Windows 专属格式。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "bili_submit.spec"

_HEAD = SPEC.read_text(encoding="utf-8").split("a = Analysis(")[0]
_WHOLE = SPEC.read_text(encoding="utf-8")


def _exec_head(env: dict[str, str] | None = None, platform: str | None = None) -> dict:
    """执行 spec 前半段，可选地把平台伪装成另一个。

    spec 用的是 ``import sys as _sys``，``IS_WINDOWS`` 读的是
    ``_sys.platform``——属性访问，所以 monkeypatch ``sys.platform``
    就能让 spec 以为自己在另一台机器上。CI 跑在 Linux 上，不伪装就
    **永远测不到 Windows 那条分支**。
    """
    ns: dict = {"SPECPATH": str(ROOT), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        if platform:
            mp.setattr(sys, "platform", platform)
        for key, value in (env or {}).items():
            mp.setenv(key, value)
        exec(compile(_HEAD, str(SPEC), "exec"), ns)
    return ns


# ---------------------------------------------------------------------------
# 打包形态
# ---------------------------------------------------------------------------

def test_appimage_flag_switches_to_onedir():
    """``APPIMAGE=1`` 必须真的切到 onedir。

    Linux 用 AppImage 而不是 exe，形态必须走目录：AppImage 的工作方式
    就是「一个文件装着整个目录」（squashfs 挂载）。走 onefile 的话
    每次启动都要把整个归档解到 ``/tmp``，而这个解出来的目录**还在
    AppImage 自己的挂载点里**，等于白付一次解压。
    """
    ns = _exec_head({"APPIMAGE": "1"}, platform="linux")
    assert ns["APPIMAGE"] is True
    assert ns["ONEDIR"] is True


def test_windows_behaviour_is_untouched():
    """Windows 那两条路径必须和加 Linux 之前一模一样。

    这是整个改动的安全边界：``INSTALLER`` 的语义、默认值、以及
    「不设就走 onefile」这条，都不该因为多了个 ``APPIMAGE`` 而变。
    伪装成 win32 来测——否则在 Linux runner 上这条恒等于只测了 Linux。
    """
    plain = _exec_head(platform="win32")
    assert plain["ONEDIR"] is False, "Windows 上不设开关时仍应是 onefile"
    assert plain["APPIMAGE"] is False

    installer = _exec_head({"INSTALLER": "1"}, platform="win32")
    assert installer["ONEDIR"] is True, "INSTALLER=1 在 Windows 上仍应切 onedir"

    # Linux 的开关不该在 Windows 上意外生效（比如哪天有人把判断写反）
    cross = _exec_head({"APPIMAGE": "1"}, platform="win32")
    assert cross["ONEDIR"] is True, "APPIMAGE 是显式开关，两平台都该生效"


def test_installer_flag_line_is_still_verbatim():
    """``ONEDIR`` 那行的原文不能被动过。

    ``tests/test_packaging.py`` 里有一整组断言是按源码字符串匹配的
    （``ONEDIR = os.environ.get("INSTALLER") == "1"`` 逐字）。把它改成
    ``os.environ.get("INSTALLER") == "1" or APPIMAGE`` 之类的合并写法，
    那边的守卫会一次红六条——留这条断言是为了让「为什么必须分两行写」
    这件事有个出处，而不是让人觉得那样写是随手为之。
    """
    assert 'ONEDIR = os.environ.get("INSTALLER") == "1"' in _WHOLE
    # 分叉写成独立的 if，不并进上面那行
    assert "if APPIMAGE:" in _WHOLE


def test_appimage_refuses_to_embed_ffmpeg():
    """``APPIMAGE=1`` + ``BUNDLE_FFMPEG=1`` 也要被拒。

    复用 INSTALLER 那条冲突检查：onedir 的 ffmpeg 由构建脚本放到 exe
    同目录。Linux 上这条更重要——squashfs 是只读挂载，用户换不了里面
    的文件，而外置 ffmpeg 是「用户能自己换版本」的唯一途径。
    """
    ns: dict = {"SPECPATH": str(ROOT), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "linux")
        mp.setenv("APPIMAGE", "1")
        mp.setenv("BUNDLE_FFMPEG", "1")
        with pytest.raises(SystemExit):
            exec(compile(_HEAD, str(SPEC), "exec"), ns)


# ---------------------------------------------------------------------------
# ffmpeg：Linux 上没有 .exe 后缀
# ---------------------------------------------------------------------------

def test_ffmpeg_is_found_without_the_exe_suffix(tmp_path):
    """Linux 上 ``vendor/ffmpeg`` 必须能被认出来。

    只找 ``ffmpeg.exe`` 的话，Linux 的每一次 GUI 构建都会卡在
    SystemExit 上，而那句报错通篇在讲 Windows 的修法
    （``setup_ffmpeg.py`` / ``FFMPEG_EXE``），看的人只会更困惑。
    """
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    expected = vendor / "ffmpeg"
    expected.write_bytes(b"\x7fELF")

    ns: dict = {"SPECPATH": str(tmp_path), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "linux")
        mp.delenv("FFMPEG_EXE", raising=False)
        exec(compile(_HEAD, str(SPEC), "exec"), ns)
        assert ns["_resolve_ffmpeg_exe"]() == expected


def test_linux_prefers_the_extensionless_ffmpeg(tmp_path):
    """两个都在时，Linux 挑**无扩展名**的那个。

    同一台机器上混着 Windows 版和 Linux 版并不罕见（两边都往
    ``vendor/`` 放过 ffmpeg）。固定成「.exe 优先」的话，Linux 会挑中
    一个自己执行不了的文件——它确实存在、体积也正常，一路到运行时才
    报 ``Exec format error``，而那句报错完全指不到「挑错了文件」。
    """
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / "ffmpeg.exe").write_bytes(b"MZ windows build")
    wanted = vendor / "ffmpeg"
    wanted.write_bytes(b"\x7fELF linux build")

    ns: dict = {"SPECPATH": str(tmp_path), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "linux")
        mp.delenv("FFMPEG_EXE", raising=False)
        exec(compile(_HEAD, str(SPEC), "exec"), ns)
        assert ns["_resolve_ffmpeg_exe"]() == wanted

    # 反过来：Windows 上必须继续挑 .exe 的那个
    ns = {"SPECPATH": str(tmp_path), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "win32")
        mp.delenv("FFMPEG_EXE", raising=False)
        exec(compile(_HEAD, str(SPEC), "exec"), ns)
        assert ns["_resolve_ffmpeg_exe"]() == vendor / "ffmpeg.exe"


def test_ffmpeg_keeps_its_executable_bit_on_linux(tmp_path):
    """Linux 内嵌 ffmpeg 必须走 ``binaries``，不能走 ``datas``。

    这是最有 Linux 特色的一处：**datas 不保留可执行位**。拷出来的
    ffmpeg 是个 mode 644 的普通文件，运行时报 ``Permission denied``——
    而那句报错看起来跟「ffmpeg 坏了」「ffmpeg 不兼容」一模一样，
    排查方向会被彻底带偏。

    Windows 反过来：能否执行只看扩展名，不经属性位，那边继续用 datas
    （省掉 bindepend 对几十 MB 二进制的漫长扫描）。
    """
    fake = tmp_path / "ffmpeg"
    fake.write_bytes(b"\x7fELF")

    ns = _exec_head({"BUNDLE_FFMPEG": "1", "FFMPEG_EXE": str(fake)},
                    platform="linux")
    assert any("ffmpeg" in Path(str(item[0])).name for item in ns["binaries"]), (
        "Linux 上 ffmpeg 必须进 binaries——datas 会丢掉可执行位"
    )
    assert not any("ffmpeg" in Path(str(item[0])).name for item in ns["datas"])


def test_windows_still_embeds_ffmpeg_via_datas(tmp_path):
    """Windows 侧必须继续走 datas，别被上一条改带偏。

    同一份代码两个平台，改一边容易顺手改了另一边。Windows 换 binaries
    不会坏（bindepend 只是慢），所以坏了也没人会发现——只有这条守卫会响。
    """
    fake = tmp_path / "ffmpeg.exe"
    fake.write_bytes(b"MZ")

    ns = _exec_head({"BUNDLE_FFMPEG": "1", "FFMPEG_EXE": str(fake)},
                    platform="win32")
    assert any("ffmpeg" in Path(str(item[0])).name for item in ns["datas"]), (
        "Windows 上 ffmpeg 应继续走 datas（省掉 bindepend 的漫长扫描）"
    )
    assert not any("ffmpeg" in Path(str(item[0])).name for item in ns["binaries"])


# ---------------------------------------------------------------------------
# 只在 Windows 上成立的两个参数
# ---------------------------------------------------------------------------

def _param_block(name: str) -> str:
    """取出 ``EXE(...)`` 里某个参数的**整段**（可能跨行）。

    不能只取 ``name=`` 那一行：``version=`` 写成了一个三行的条件
    表达式，``IS_WINDOWS`` 落在第二行。只看第一行的话，守卫会红在
    一个其实是对的写法上——而修的人最可能的做法就是把守卫删掉。
    判「参数结束」用「缩进相同的行且以逗号结尾」。
    """
    lines = _WHOLE.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip().startswith(name)]
    assert starts, f"spec 里找不到 {name} 参数"
    assert len(starts) == 1, f"{name} 出现了 {len(starts)} 次"
    begin = starts[0]
    indent = len(lines[begin]) - len(lines[begin].lstrip())
    chunk: list[str] = []
    for line in lines[begin: begin + 6]:
        chunk.append(line)
        stripped = line.strip()
        if stripped.endswith(",") and (len(line) - len(line.lstrip())) == indent:
            break
    return "\n".join(chunk)


def test_icon_is_windows_only():
    """``icon=`` 只在 Windows 上给。

    PyInstaller 的 ``EXE(icon=...)`` 是 Windows 的版本资源槽，只认
    ``.ico``。Linux 上传过去要么被忽略（白高兴），要么因为读不了那个
    格式直接让构建失败。Linux 的窗口图标走别处——AppImage 的
    ``.desktop`` 文件（见 build_linux.sh）。
    """
    assert "IS_WINDOWS" in _param_block("icon="), (
        f"icon= 没有按平台守卫：\n{_param_block('icon=')}"
    )


def test_version_resource_is_windows_only():
    """``version=`` 同样只在 Windows 上给。

    ``assets/version_info.txt`` 是烧进 exe 资源段的版本资源，Linux 的
    ELF 里没有这东西。留着它在 Linux 上不会报错，但会让人以为
    「Linux 版的版本信息也设好了」——而实际上 ELF 里根本没有。
    """
    assert "IS_WINDOWS" in _param_block("version="), (
        f"version= 没有按平台守卫：\n{_param_block('version=')}"
    )


# ---------------------------------------------------------------------------
# tkinter：Linux 上它不是 Python 自带的
# ---------------------------------------------------------------------------

def test_gui_mode_fails_loudly_when_tkinter_is_missing():
    """GUI 模式下 tkinter 不可用必须当场失败。

    Linux 上 tkinter 由发行版单独打包（``python3-tk``），**pip 装不了**。
    缺了它 PyInstaller 一声不吭：它只是安静地打出一个不含 tkinter 的
    包——文件齐全、体积正常、ffmpeg 也在，而界面永远起不来。这是唯一
    能在构建期发现它的地方。
    """
    ns: dict = {"SPECPATH": str(ROOT), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "linux")
        # sys.modules 里放 None 会让 import 抛 ImportError
        mp.setitem(sys.modules, "tkinter", None)
        mp.setenv("GUI", "1")
        mp.setenv("NO_FFMPEG", "1")
        with pytest.raises(SystemExit) as exc:
            exec(compile(_HEAD, str(SPEC), "exec"), ns)

    message = str(exc.value)
    assert "tkinter" in message
    # 报错要给出**各发行版**的装法：这句是给一个不知道 tkinter 还要
    # 单独装的人看的，只说「缺 tkinter」等于没说。
    assert "python3-tk" in message, (
        f"报错没给出 Debian/Ubuntu 的装法：{message}"
    )
    assert "pip" in message.lower(), (
        "应点明 tkinter 装不了 pip——不然人的第一反应就是去 pip install"
    )
    # spec 的消息必须 ASCII：Windows 上 stdout 是 cp1252，中文会抛
    # UnicodeEncodeError，而崩在打包阶段很难看出是编码问题。
    assert message.isascii(), f"spec 的报错消息含非 ASCII 字符：{message!r}"


def test_cli_mode_does_not_demand_tkinter():
    """命令行版不该被 tkinter 拖住。

    服务器上打一个只跑 CLI 的包很常见，那里没有也不需要 tkinter。
    GUI 的检查开成全局的话，这类机器连命令行版都打不出来。
    """
    ns: dict = {"SPECPATH": str(ROOT), "__name__": "spec_probe"}
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(sys, "platform", "linux")
        mp.setitem(sys.modules, "tkinter", None)
        mp.delenv("GUI", raising=False)
        # 不抛 SystemExit 即通过
        exec(compile(_HEAD, str(SPEC), "exec"), ns)
    assert ns["GUI"] is False


def test_spec_admins_it_runs_on_both_platforms():
    """spec 顶部的说明不能还写着「必须在 Windows 上执行」。

    PyInstaller 不是交叉编译器——这句是对的，但结论不是「所以只能用
    Windows」，而是「所以要**在目标平台上**跑」。写错的话，下一个想在
    Linux 上打包的人读到它会直接放弃，转而去维护一份重复的 spec。
    """
    head = _WHOLE.split('"""')[1] if '"""' in _WHOLE else ""
    assert "build_linux.sh" in head, (
        "spec 的开头没提 Linux 的打包入口——它现在支持两个平台了"
    )
    assert "交叉编译器" in head, (
        "spec 开头应保留「PyInstaller 不是交叉编译器」这句前提，"
        "否则会有人以为能在一台机器上打出两个平台的包"
    )
