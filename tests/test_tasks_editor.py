"""``TaskEditor`` 的直连单测。

这些用例**不需要建整个批量任务页**——``TaskEditor`` 的依赖是从构造函数
注入的，几个替身就能单独跑起来。这正是把它从 ``TasksView`` 里分出来的
主要理由：原先要验证「套模板只改勾选行」，得先建 App、选目录、等扫描
线程跑完，而现在只需要一个 list 和两个假对象。

这里刻意**不测** ``TasksView`` 上的转发方法（``_edit_titles`` /
``_apply_title_template``），那些由 ``test_tasks.py`` 覆盖。两套并存是
有意的：转发层退役时（下一轮），这里的用例一条都不用改。
"""

from __future__ import annotations

from dataclasses import replace

from bilibili_submit.config import TaskConfig
from bilibili_submit.ui.views.tasks_editor import TaskEditor, render_title_template


class _FakeTree:
    """只实现 ``TaskEditor`` 用到的 ``set``——够用就行。"""

    def __init__(self) -> None:
        self.cells: dict[tuple[str, str], str] = {}

    def set(self, iid: str, column: str, value: str) -> None:
        self.cells[(iid, column)] = value


class _FakeLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append(self, text: str) -> None:
        self.lines.append(text)


def _editor(tmp_path, tasks, picked, *, template="", asker=None):
    """造一个 TaskEditor，配好最小替身。"""
    tree = _FakeTree()
    log = _FakeLog()
    saved: list[int] = []

    def write_task(index, task):
        tasks[index] = task

    editor = TaskEditor(
        None,  # 不弹对话框时 master 用不到
        tree=tree,
        log=log,
        read_tasks=lambda: tasks,
        write_task=write_task,
        selected_indexes=lambda: list(picked),
        read_template=lambda: template,
        save_state=lambda: saved.append(1),
        asker=asker or (lambda *a, **k: None),
    )
    return editor, tree, log, saved


def _tasks(tmp_path, *names):
    """造几个真实存在的单文件任务。"""
    out = []
    for name in names:
        path = tmp_path / f"{name}.mp4"
        path.write_bytes(b"")
        out.append(TaskConfig(name=name, type="single", file=str(path), title=""))
    return out


# ---------- 模板渲染 ----------


def test_template_substitutes_both_placeholders():
    assert render_title_template("{name} 第{n}集", "旅行", 2) == "旅行 第2集"
    assert render_title_template("第{n}集 {name}", "旅行", 1) == "第1集 旅行"


def test_template_without_placeholders_is_one_fixed_title():
    """没写占位符就是固定标题——不去猜，也不报错。"""
    assert render_title_template("合集", "旅行", 3) == "合集"


# ---------- 套模板 ----------


def test_apply_template_only_touches_picked_rows(tmp_path):
    """只动勾选的行。

    列表里可能有一半已经手动改过标题，全量套一遍等于把那些手工活全冲掉。
    """
    tasks = _tasks(tmp_path, "旅行_01", "旅行_02", "教程")
    tasks[2] = replace(tasks[2], title="手工改过的标题")
    editor, tree, _log, _saved = _editor(
        tmp_path, tasks, picked=[0, 1], template="{name} 第{n}集"
    )

    editor.apply_template()

    assert tasks[0].title == "旅行_01 第1集"
    assert tasks[1].title == "旅行_02 第2集"
    # 没勾的那行一个字都不许动
    assert tasks[2].title == "手工改过的标题"


def test_apply_template_numbers_follow_pick_order(tmp_path):
    """``{n}`` 是勾选顺序，不是行号——勾了第 1、3 行就得到 1、2。"""
    tasks = _tasks(tmp_path, "a", "b", "c")
    editor, _tree, _log, _saved = _editor(
        tmp_path, tasks, picked=[0, 2], template="第{n}集"
    )

    editor.apply_template()

    assert tasks[0].title == "第1集"
    assert tasks[2].title == "第2集"


def test_apply_template_updates_the_visible_cell(tmp_path):
    """改了数据也要改界面上那一格，否则列表显示的还是旧标题。"""
    tasks = _tasks(tmp_path, "旅行_01")
    editor, tree, _log, _saved = _editor(
        tmp_path, tasks, picked=[0], template="{name} 第{n}集"
    )

    editor.apply_template()

    assert tree.cells[("0", "title")] == "旅行_01 第1集"


def test_apply_template_saves_state(tmp_path):
    """套完要存盘——下次打开还得是这个标题。"""
    tasks = _tasks(tmp_path, "旅行_01")
    editor, _tree, _log, saved = _editor(
        tmp_path, tasks, picked=[0], template="{name}"
    )

    editor.apply_template()

    assert saved == [1]


def test_apply_template_explains_when_template_is_empty(tmp_path):
    """空模板要说人话，不能默默什么都不做。"""
    tasks = _tasks(tmp_path, "旅行_01")
    editor, _tree, log, saved = _editor(tmp_path, tasks, picked=[0], template="   ")

    editor.apply_template()

    assert tasks[0].title == ""
    assert any("投稿标题是空的" in line for line in log.lines)
    assert saved == []  # 什么都没改就别存盘


def test_apply_template_explains_when_nothing_is_picked(tmp_path):
    tasks = _tasks(tmp_path, "旅行_01")
    editor, _tree, log, saved = _editor(
        tmp_path, tasks, picked=[], template="{name}"
    )

    editor.apply_template()

    assert any("没勾任何任务" in line for line in log.lines)
    assert saved == []


def test_apply_template_reuses_the_edited_title_as_name(tmp_path):
    """改过标题的任务用改后的名字当 ``{name}``，不然手工活会被冲掉一半。"""
    tasks = _tasks(tmp_path, "旅行_01")
    tasks[0] = replace(tasks[0], title="我的旅行")
    editor, _tree, _log, _saved = _editor(
        tmp_path, tasks, picked=[0], template="{name} 第{n}集"
    )

    editor.apply_template()

    assert tasks[0].title == "我的旅行 第1集"


# ---------- 改单个稿件 ----------


def test_edit_writes_archive_title_back(tmp_path):
    tasks = _tasks(tmp_path, "旅行_01")
    editor, tree, log, _saved = _editor(
        tmp_path, tasks, picked=[], asker=lambda *a, **k: ("旅行日记", [])
    )

    editor.edit("0")

    assert tasks[0].title == "旅行日记"
    assert tree.cells[("0", "title")] == "旅行日记"
    assert any("已更新" in line for line in log.lines)


def test_edit_does_not_write_part_titles_for_single_file(tmp_path):
    """单 P 写 part_titles 没有意义——它只有一个分 P，标题就是稿件标题。"""
    tasks = _tasks(tmp_path, "旅行_01")
    editor, _tree, _log, _saved = _editor(
        tmp_path,
        tasks,
        picked=[],
        # 对话框就算返回了分 P 标题也不该被写进去
        asker=lambda *a, **k: ("旅行日记", ["不该被写入"]),
    )

    editor.edit("0")

    assert tasks[0].title == "旅行日记"
    assert not tasks[0].part_titles


def test_edit_writes_part_titles_for_multi_file(tmp_path):
    tasks = [
        TaskConfig(
            name="旅行",
            type="multip",
            files=[str(tmp_path / "旅行_01.mp4"), str(tmp_path / "旅行_02.mp4")],
            title="",
        )
    ]
    for path in (tmp_path / "旅行_01.mp4", tmp_path / "旅行_02.mp4"):
        path.write_bytes(b"")
    editor, _tree, log, _saved = _editor(
        tmp_path, tasks, picked=[], asker=lambda *a, **k: ("旅行日记", ["P1名", "P2名"])
    )

    editor.edit("0")

    assert tasks[0].title == "旅行日记"
    assert tasks[0].part_titles == ["P1名", "P2名"]
    assert any("2 个分P标题" in line for line in log.lines)


def test_edit_keeps_everything_when_the_dialog_is_cancelled(tmp_path):
    tasks = _tasks(tmp_path, "旅行_01")
    editor, tree, log, _saved = _editor(
        tmp_path, tasks, picked=[], asker=lambda *a, **k: None
    )

    editor.edit("0")

    assert tasks[0].title == ""
    assert tree.cells == {}
    assert log.lines == []


def test_edit_skips_a_task_without_any_file(tmp_path):
    """一个文件都没有的任务不弹对话框——弹了也只能填个没有落点的标题。"""
    tasks = [TaskConfig(name="空任务", type="single")]
    asked: list = []
    editor, tree, log, _saved = _editor(
        tmp_path, tasks, picked=[], asker=lambda *a, **k: asked.append(1)
    )

    editor.edit("0")

    assert asked == [], "没有文件的任务不该弹对话框"
    assert tree.cells == {}
    assert log.lines == []


def test_edit_ignores_an_iid_out_of_range(tmp_path):
    """越界直接返回，不抛异常——iid 来自表格，防一手。"""
    tasks = _tasks(tmp_path, "旅行_01")
    editor, _tree, _log, _saved = _editor(
        tmp_path, tasks, picked=[], asker=lambda *a, **k: ("x", [])
    )

    editor.edit("9")

    assert tasks[0].title == ""


def test_edit_passes_the_current_title_into_the_dialog(tmp_path):
    """对话框要先显示现有标题，不然用户是在盲改。"""
    tasks = _tasks(tmp_path, "旅行_01")
    tasks[0] = replace(tasks[0], title="现有标题")
    seen: list = []
    editor, _tree, _log, _saved = _editor(
        tmp_path,
        tasks,
        picked=[],
        asker=lambda archive, files, current, archive_title: seen.append(
            (archive, archive_title)
        ),
    )

    editor.edit("0")

    assert seen == [("旅行_01", "现有标题")]
