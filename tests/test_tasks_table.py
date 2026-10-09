"""``TaskTable`` 的直连单测。

和 ``test_tasks.py`` 里那些用例的区别：那边是建起整个批量任务页再读
``view._tree``，这里是只建一个 Treeview 就测表格自己的行为。拆出来之后
才可能这么测——原先这些逻辑都长在 ``TasksView`` 上，要验证「缺失文件不
预选」得先建 App、选目录、等扫描线程跑完。

需要 Tk（Treeview 是真的控件），所以挂 ``needs_display``。
"""

from __future__ import annotations

from tkinter import ttk

from bilibili_submit.config import TaskConfig
from bilibili_submit.ui.views.tasks_table import PICKED, UNPICKED, TaskTable
from conftest import needs_display


def _table(root, *, editable=True, tid=21, source_mode="folder"):
    """造一个 TaskTable，所有外部依赖换成记录用的替身。"""
    holder = ttk.Frame(root)
    holder.grid(row=0, column=0)
    holder.columnconfigure(0, weight=1)
    holder.rowconfigure(0, weight=1)

    logs: list[str] = []
    changed: list[int] = []
    filled: list[bool] = []
    state = {"editable": editable}

    table = TaskTable(
        holder,
        append_log=logs.append,
        is_editable=lambda: state["editable"],
        on_changed=lambda: changed.append(1),
        on_filled=filled.append,
        read_tid=lambda: tid,
        source_mode=lambda: source_mode,
    )
    table.tree.grid(row=0, column=0, sticky="nsew")
    return table, logs, changed, filled, state


def _tasks(tmp_path, *names, missing=()):
    """造任务；``missing`` 里的名字只建 TaskConfig 不建文件。"""
    out = []
    for name in names:
        path = tmp_path / f"{name}.mp4"
        if name not in missing:
            path.write_bytes(b"x")
        out.append(TaskConfig(name=name, type="single", file=str(path), title=""))
    return out


# ---------- 填充 ----------


@needs_display
def test_fill_picks_existing_rows_by_default(root, tmp_path):
    """正常任务默认勾选——大多数情况用户就是要跑全部。"""
    table, _logs, _changed, filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "旅行_01", "旅行_02"))

    assert table.picked == {"0": True, "1": True}
    assert filled == [True]


@needs_display
def test_fill_does_not_pick_missing_rows(root, tmp_path):
    """文件不存在的行不预选，也记进 missing。"""
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "存在", "没了", missing=("没了",)))

    assert table.picked["0"] is True
    assert table.picked["1"] is False
    assert table.missing == {"1"}
    assert table.tones["1"] == "missing"
    assert table.tree.item("1", "values")[0] == UNPICKED
    assert table.tree.item("0", "values")[0] == PICKED


@needs_display
def test_fill_reports_whether_there_are_any_rows(root, tmp_path):
    """空列表要通知外部——那是切占位图的信号。"""
    table, _logs, _changed, filled, _state = _table(root)
    table.fill([])
    table.fill(_tasks(tmp_path, "a"))

    assert filled == [False, True]


@needs_display
def test_fill_clears_the_previous_batch(root, tmp_path):
    """换一批任务时旧行的 id 不该还留在表里。"""
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a", "b", "c"))
    table.fill(_tasks(tmp_path, "新的一批"))

    assert list(table.tree.get_children()) == ["0"]
    assert table.tree.item("0", "values")[1] == "新的一批"


# ---------- 勾选 ----------


@needs_display
def test_toggle_flips_a_row(root, tmp_path):
    table, _logs, changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a"))

    table.toggle("0")
    assert table.picked["0"] is False
    assert table.tree.item("0", "values")[0] == UNPICKED

    table.toggle("0")
    assert table.picked["0"] is True
    assert table.tree.item("0", "values")[0] == PICKED
    # 每次改动都要通知外部刷新汇总
    assert len(changed) >= 2


@needs_display
def test_toggle_refuses_a_missing_row(root, tmp_path):
    """缺失行拒绝勾选：勾了却在执行时被跳过最让人困惑。"""
    table, logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "没了", missing=("没了",)))

    table.toggle("0")

    assert table.picked["0"] is False
    assert any("该文件不存在" in line for line in logs)


@needs_display
def test_toggle_is_ignored_while_locked(root, tmp_path):
    """执行期间锁定勾选——界面上的选择和实际执行的列表不能脱节。"""
    table, _logs, _changed, _filled, state = _table(root)
    table.fill(_tasks(tmp_path, "a"))
    state["editable"] = False

    table.toggle("0")

    assert table.picked["0"] is True  # 没动


@needs_display
def test_set_pick_never_picks_a_missing_row(root, tmp_path):
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "没了", missing=("没了",)))

    table.set_pick("0", True)

    assert table.picked["0"] is False


# ---------- 批量选择 ----------


@needs_display
def test_select_failed_picks_only_the_error_rows(root, tmp_path):
    """只勾失败项——文件缺失的不算（它们根本没投过）。"""
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a", "b", "c", "没了", missing=("没了",)))
    table.mark(0, "失败", "error")
    table.mark(1, "成功", "ok")

    table.select_failed()

    assert table.picked["0"] is True
    assert table.picked["1"] is False
    assert table.picked["2"] is False
    assert table.picked["3"] is False


@needs_display
def test_any_unpicked_ignores_missing_rows(root, tmp_path):
    """缺失行永远勾不上，不能因为它们在就一直显示「全选」。

    否则点表头永远是全选，永远切不到全不选。
    """
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a", "没了", missing=("没了",)))

    assert table.any_unpicked() is False   # a 已勾，缺失的不算
    table.select_none()
    assert table.any_unpicked() is True


# ---------- 选中行 ----------


@needs_display
def test_selected_indexes_excludes_missing(root, tmp_path):
    """缺失行**就算被强行设成勾上**也不能进执行列表。

    光验证「默认不勾」不够——那只说明 fill 没勾它。真正的保证在
    ``selected_indexes`` 自己身上：它是执行列表的唯一来源，必须自己
    再挡一次。
    """
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a", "没了", "b", missing=("没了",)))
    table.picked["1"] = True  # 强行勾上缺失行

    assert table.selected_indexes() == [0, 2]


# ---------- 行状态 ----------


@needs_display
def test_mark_updates_status_and_tone(root, tmp_path):
    table, _logs, changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a"))

    before = len(changed)
    table.mark(0, "投稿中", "busy")

    values = table.tree.item("0", "values")
    assert values[-1] == "投稿中"
    assert table.tones["0"] == "busy"
    assert table.tree.item("0", "tags") == ("busy",)
    # 行状态一变就必须通知外部刷新汇总，否则会出现「列表里有两个红的，
    # 汇总却说 0 失败」
    assert len(changed) > before


@needs_display
def test_mark_ignores_a_row_that_is_not_there(root, tmp_path):
    """索引越界直接返回——行可能已经被换掉了。"""
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a"))

    table.mark(9, "失败", "error")

    assert table.tones.get("9") is None


@needs_display
def test_mark_leaves_only_one_tag_per_row(root, tmp_path):
    """每行只打一个 tag——多个 tag 的背景色会互相打架。"""
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a"))
    table.mark(0, "成功", "ok")
    table.mark(0, "失败", "error")

    assert table.tree.item("0", "tags") == ("error",)


# ---------- 重置 ----------


@needs_display
def test_reset_clears_every_per_row_container(root, tmp_path):
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a", "没了", missing=("没了",)))
    table.errors["0"] = "出错了"

    table.reset()

    assert table.picked == {}
    assert table.missing == set()
    assert table.errors == {}
    assert table.tones == {}


# ---------- 列宽 ----------


@needs_display
def test_resize_columns_ignores_a_tiny_width(root, tmp_path, monkeypatch):
    """还没布局出来时（宽度 < 100）别拿 1px 去算比例。

    不然窗口最小化的瞬间所有列会被压到下限，恢复后又得重排一遍。
    """
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a"))
    # 刻意设成一个和列宽下限不同的值：设成下限的话，「去掉最小宽度判断」
    # 这个改动算出来的结果会和预期值撞上，测试就变成假绿了
    table.tree.column("name", width=999)
    monkeypatch.setattr(table.tree, "winfo_width", lambda: 50)

    table.resize_columns()

    assert table.tree.column("name", "width") == 999


@needs_display
def test_resize_columns_spreads_the_space_by_weight(root, tmp_path, monkeypatch):
    """宽度够时按权重分配——标题列权重高，就该比勾选列宽得多。"""
    table, _logs, _changed, _filled, _state = _table(root)
    table.fill(_tasks(tmp_path, "a"))
    monkeypatch.setattr(table.tree, "winfo_width", lambda: 900)

    table.resize_columns()

    pick = table.tree.column("pick", "width")
    title = table.tree.column("title", "width")
    assert title > pick * 2, f"标题列 {title} 应远宽于勾选列 {pick}"
    # 分配出去的总和不能超过可用宽度（算法会留滚动条余量）
    total = sum(table.tree.column(c, "width") for c in table.tree["columns"])
    assert total <= 900
