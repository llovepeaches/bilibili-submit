"""UI 文案不许出现 Markdown 语法。

tk 的 Label / Text / Entry 都是纯文本控件，不解析 Markdown。写在 hint、
subtitle、按钮文案里的 ``**加粗**`` 和反引号会原样显示给用户——看起来
像渲染失败的 bug。

这条守卫用 AST 扫源码里的字符串字面量，排除 docstring（docstring 里写
Markdown 是合法的，而且只能在源码里读，永远不会显示给用户）。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

UI_ROOT = pathlib.Path(__file__).resolve().parents[1] / "bilibili_submit" / "ui"


def _docstring_ids(tree: ast.AST) -> set[int]:
    """收集模块 / 类 / 函数第一条语句上的字符串常量——它们是 docstring。"""
    ids: set[int] = set()

    def take(body: list[ast.stmt]) -> None:
        if not body:
            return
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))

    take(tree.body)  # 模块级
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            take(node.body)
    return ids


def _looks_like_markdown(text: str) -> str | None:
    """命中就返回命中的语法名，没命中返回 None。"""
    if "**" in text:
        return "加粗星号 **"
    if "`" in text:
        return "反引号 `"
    if "[ttp" in text.lower() and "](" in text:
        return "Markdown 链接 []()"
    return None


def _user_visible_strings(py: pathlib.Path) -> list[tuple[int, str]]:
    """返回该文件里「不是 docstring」的字符串字面量及其行号。"""
    tree = ast.parse(py.read_text(encoding="utf-8"))
    docs = _docstring_ids(tree)
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docs
        ):
            found.append((node.lineno, node.value))
    return found


def _collect_files() -> list[pathlib.Path]:
    files = sorted(UI_ROOT.rglob("*.py"))
    assert files, f"没找到 UI 源码，检查路径：{UI_ROOT}"
    return files


@pytest.mark.parametrize("py", _collect_files(), ids=lambda p: p.name)
def test_ui_source_has_no_markdown_in_user_visible_copy(py: pathlib.Path) -> None:
    offenders = [
        f"L{lineno}: {kind} -> {' '.join(text.split())[:80]}"
        for lineno, text in _user_visible_strings(py)
        if (kind := _looks_like_markdown(text))
    ]
    assert not offenders, (
        f"{py.name} 里有 {len(offenders)} 处 Markdown 语法会原样显示给用户：\n"
        + "\n".join(f"  · {item}" for item in offenders)
    )


def test_scanner_actually_sees_markdown() -> None:
    """反向守卫：扫描器本身得真能抓到 Markdown，否则上一条是恒真的假绿。"""
    source = 'HINT = "改这里要**重启**才生效"\n'
    tree = ast.parse(source)
    docs = _docstring_ids(tree)
    hits = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docs
        and _looks_like_markdown(node.value)
    ]
    assert hits == ["改这里要**重启**才生效"]


def test_scanner_ignores_docstrings() -> None:
    """反向守卫：docstring 里的 Markdown 必须被放过，否则上一条会误伤合法写法。"""
    source = 'def f():\n    """``-101`` Cookie 失效。"""\n    return "纯文本"\n'
    tree = ast.parse(source)
    docs = _docstring_ids(tree)
    hits = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docs
        and _looks_like_markdown(node.value)
    ]
    assert hits == []
