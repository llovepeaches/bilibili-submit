"""卡顿优化的守卫测试。

`tools/bench_ui.py` 给出的是**数字**，这里挡的是**回退**：每一条都对应
一次真实的性能修复，改法被撤掉时这里要红，而不是等下一次有人拖窗口
觉得发涩才发现。

每条注释都写明「撤掉优化会怎样」，否则半年后没人知道这行断言在守什么。
"""
from __future__ import annotations

import json
import tkinter as tk
from tkinter import ttk

from conftest import needs_display

from bilibili_submit.ui import theme
from bilibili_submit.ui.views.history import HistoryView
from bilibili_submit.ui.widgets import LogConsole


def _style_root(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    theme.apply(style, "light")


# ---------- 列宽：Configure 风暴 ----------


@needs_display
def test_resize_columns_does_nothing_when_the_width_has_not_moved(root):
    """宽度没变时一次 ``column()`` 都不许写。

    ``<Configure>`` 在布局的每一步都往表格派发一次，切一页能派发上百个。
    撤掉宽度缓存的话，每次派发都要写七列——拖窗口发涩就是这么来的。

    这里数的是**调用次数**而不是耗时：耗时在 Xvfb 上抖得厉害，
    次数是确定的，撤掉缓存必然从 0 变成 7。
    """
    from bilibili_submit.ui.views.tasks_table import TaskTable

    _style_root(root)
    table = TaskTable(
        root,
        append_log=lambda m: None,
        is_editable=lambda: True,
        on_changed=lambda: None,
        on_filled=lambda has: None,
        read_tid=lambda: "21",
        source_mode=lambda: "files",
    )
    table.tree.pack(fill="both", expand=True)
    root.update_idletasks()

    calls: list[str] = []
    real = table.tree.column
    table.tree.column = (  # type: ignore[method-assign]
        lambda column, *a, **kw: calls.append(column) or real(column, *a, **kw)
    )

    width = table.tree.winfo_width()
    assert width >= 100, f"测试环境没把表格布局出来（宽 {width}）"
    for _ in range(20):
        table.resize_columns()

    assert calls == [], f"宽度没变却写了 {len(calls)} 次列宽：{calls[:8]}"


@needs_display
def test_resize_columns_still_recomputes_when_the_width_moves(root):
    """宽度真的变了就得重算——缓存不许把「该动的」也挡掉。

    上一条只挡了重复，这条守另一半：挡过头的话窗口拉宽后列宽不跟着变，
    右侧会空一大片，那比抖还难看。
    """
    from bilibili_submit.ui.views.tasks_table import TaskTable

    _style_root(root)
    table = TaskTable(
        root,
        append_log=lambda m: None,
        is_editable=lambda: True,
        on_changed=lambda: None,
        on_filled=lambda has: None,
        read_tid=lambda: "21",
        source_mode=lambda: "files",
    )
    table.tree.pack(fill="both", expand=True)

    step = [0]

    def varying() -> int:
        """每次调用都比上次宽 100px——模拟真的在把窗口拉宽。"""
        step[0] += 1
        # 900 起步：总宽低于各列下限之和时算出来全是下限，几档宽度会
        # 撞成同一个值，测试就假绿了
        return 900 + 100 * step[0]

    table.tree.winfo_width = varying  # type: ignore[method-assign]
    widths: list[int] = []
    try:
        for _ in range(3):
            table.resize_columns()
        widths.append(table.tree.column("title", "width"))
        table.resize_columns()
        widths.append(table.tree.column("title", "width"))
    finally:
        del table.tree.winfo_width

    assert widths[0] != widths[1], (
        f"宽度变了却没重算列宽，两帧都是 {widths[0]}"
    )


# ---------- 日志：批量写入 + 行数上限 ----------


@needs_display
def test_log_console_caps_the_line_count(root):
    """日志必须有上限。

    一次投几百个文件能堆到几万行，Text 每多一万行就多一份内存与重排
    成本，而用户只看最后几十行。撤掉 MAX_LINES 的话这段会红。
    """
    _style_root(root)
    log = LogConsole(root, height=6)
    log.pack(fill="both", expand=True)

    total = LogConsole.MAX_LINES + 500
    for i in range(total):
        log.append(f"第 {i} 行")

    # Text 末尾恒有一个换行（空 Text 里也有一条），所以真实行数要减 1
    kept = log._text.get("1.0", "end").count("\n") - 1
    assert kept <= LogConsole.MAX_LINES, (
        f"写了 {total} 行，还剩 {kept} 行（上限 {LogConsole.MAX_LINES}）"
    )
    # 丢的是**最旧的**，最后一行必须还在
    assert f"第 {total - 1} 行" in log._text.get("1.0", "end")


@needs_display
def test_log_console_defers_the_scroll_instead_of_doing_it_per_line(root):
    """写一行不许滚一次。

    ``see()`` 每次都让 Text 重排一遍再算滚动位置，逐行 see 占掉日志
    写入九成以上的时间（见 tools/bench_ui.py 里的实测）。这里数的是
    同步调用次数：撤掉 after_idle 合并，它会从 0 变成行数。
    """
    _style_root(root)
    log = LogConsole(root, height=6)
    log.pack(fill="both", expand=True)

    seen: list[int] = []
    log._text.see = lambda index: seen.append(1)  # type: ignore[method-assign]

    for i in range(50):
        log.append(f"第 {i} 行")

    assert seen == [], f"append 里同步滚了 {len(seen)} 次，应该攒到空闲时"
    # 攒下的那一次仍然会发生：内容不能停在看不见的地方
    log._flush_scroll()
    assert seen, "攒到空闲时也应该真的滚一次，否则新日志永远看不见"


@needs_display
def test_log_console_extend_writes_every_line(root):
    """``extend`` 一次写多行，一行都不能少。"""
    _style_root(root)
    log = LogConsole(root, height=6)
    log.pack(fill="both", expand=True)

    log.extend([f"第 {i} 行" for i in range(20)])

    body = log._text.get("1.0", "end")
    for i in range(20):
        assert f"第 {i} 行" in body, f"少了第 {i} 行"


# ---------- 历史页：切页不该重建 ----------


def _history_view(root: tk.Tk, tmp_path, entries: int) -> HistoryView:
    """造一个历史页，历史文件里有 ``entries`` 条记录。"""
    import bilibili_submit.scheduler as scheduler
    from bilibili_submit.ui.views import history as history_mod

    history_file = tmp_path / "history.json"
    history_file.write_text(
        json.dumps(
            [
                {"name": f"任务{i}", "bvid": f"BV{i:09d}", "time": 1_700_000_000 + i}
                for i in range(entries)
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # 两处都要改：模块在 import 时就把路径绑成默认值了
    scheduler.DEFAULT_HISTORY_FILE = str(history_file)
    history_mod.DEFAULT_HISTORY_FILE = str(history_file)

    view = HistoryView(root, None)  # type: ignore[arg-type]
    view.pack(fill="both", expand=True)
    return view


@needs_display
def test_history_refresh_skips_rebuild_when_file_unchanged(root, tmp_path):
    """文件没变就别重建。

    主窗口每次切到本页都调一次 ``refresh()``。历史文件是追加写的，
    投得越多越大，撤掉指纹比对的话，用户中途点去历史页看一眼都要
    重新 parse 一遍 JSON 再删插 200 行。
    """
    _style_root(root)
    view = _history_view(root, tmp_path, 10)
    root.update_idletasks()

    view.refresh()
    root.update_idletasks()
    assert len(view._tree.get_children()) == 10

    # 把树清空再刷一次：如果 refresh 老老实实重建，会填回 10 行；
    # 命中缓存就什么都不做，树仍然是空的。
    view._tree.delete(*view._tree.get_children())
    view.refresh()
    root.update_idletasks()

    assert view._tree.get_children() == (), (
        "文件没变却重建了整张表——切页卡顿会回来"
    )


@needs_display
def test_history_refresh_force_rebuilds_on_demand(root, tmp_path):
    """「刷新」按钮必须真的重读——缓存不许把用户主动的刷新也挡掉。"""
    _style_root(root)
    view = _history_view(root, tmp_path, 10)
    root.update_idletasks()

    view.refresh()
    root.update_idletasks()
    view._tree.delete(*view._tree.get_children())

    view.refresh(force=True)
    root.update_idletasks()

    assert len(view._tree.get_children()) == 10, "force 之后应重新填满"


@needs_display
def test_history_refresh_rebuilds_after_the_file_changes(root, tmp_path):
    """投完稿文件变了，下一次切页必须看到新记录。

    挡的是「缓存永不失效」这种改法：指纹比对了却忘了比对结果要用。
    """
    _style_root(root)
    view = _history_view(root, tmp_path, 3)
    root.update_idletasks()
    view.refresh()
    root.update_idletasks()
    assert len(view._tree.get_children()) == 3

    # 模拟一次投稿成功：追加一条并让 mtime/大小都变
    from bilibili_submit.ui.views import history as history_mod

    path = tmp_path / "history.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    entries.append({"name": "新投稿", "bvid": "BV000000001", "time": 1_700_000_999})
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")
    history_mod.DEFAULT_HISTORY_FILE = str(path)

    view.refresh()
    root.update_idletasks()

    texts = [
        view._tree.item(iid, "values") for iid in view._tree.get_children()
    ]
    assert len(texts) == 4, f"文件变了却没重建：{len(texts)} 行"
