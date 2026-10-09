"""界面测试基座的守卫。

``needs_display`` 和 ``run_until`` 曾经在 6 个 / 3 个测试文件里各写一份。
重复本身只是难看，**改一处漏五处**才是真问题：有人把 ``run_until`` 的
5 秒超时改成 0 来调试，改完忘了还原，其余两份还在用旧值——而那个「旧值」
才是正确的，于是「修好」的那份反而成了唯一在骗人的。

所以这份文件守的是两件事：这些 helper 只有一份，以及缺屏时的行为符合预期。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import _needs_display_marker

TESTS_DIR = Path(__file__).resolve().parent


def _assigned_names(path: Path) -> set[str]:
    """取模块顶层被赋值的名字（``ast``，不是文本匹配）。

    用 AST 而不是搜字符串，否则注释里写一句「这里不要再用 run_until」
    也会被当成违规。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


@pytest.mark.parametrize("name", ["needs_display", "run_until", "environment_probe_targets"])
def test_shared_helpers_are_defined_only_in_conftest(name):
    """这些 helper 只能由 ``conftest.py`` 提供，测试文件一律 import。"""
    offenders = [
        path.name
        for path in sorted(TESTS_DIR.glob("test_*.py"))
        if name in _assigned_names(path)
    ]
    assert not offenders, (
        f"{name} 在这些测试文件里被重新定义了：{offenders}。"
        f"请改成 from conftest import {name}——各写一份时改一处漏五处。"
    )


def test_require_display_mode_does_not_silently_skip():
    """``BILLI_REQUIRE_DISPLAY=1`` 时缺屏要真跑真红，不能悄悄跳过。

    这是给 CI 用的开关。如果它退化成「照旧跳过」，CI 上界面测试会整片
    静默通过——看上去全绿，实际一行都没验。
    """
    marker = _needs_display_marker(display_ok=False, require=True)
    assert marker.name == "skipif"
    assert marker.args == (False,), (
        "严格模式下 marker 必须是 skipif(False)，否则用例还是会被跳过"
    )


def test_normal_mode_skips_when_there_is_no_display():
    """没有开关且没屏时照旧跳过——本机跑测试不该因为缺屏就全红。"""
    marker = _needs_display_marker(display_ok=False, require=False)
    assert marker.name == "skipif"
    assert marker.args == (True,)
    assert "无显示环境" in marker.kwargs.get("reason", "")


def test_normal_mode_runs_when_display_is_there():
    """有屏时不跳过。"""
    marker = _needs_display_marker(display_ok=True, require=False)
    assert marker.args == (False,)
