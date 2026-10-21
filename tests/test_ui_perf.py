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

from bilibili_submit.ui import layout  # noqa: F401
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

    .. note::
       这里盯的是**写入门的值**而不是回读值：写入按
       ``total - theme.TABLE_COLUMN_MARGIN`` 算，最后 2px 由 Tk 的
       stretch 在空闲时补，而 monkeypatch 只改了 ``winfo_width`` 的
       返回值、树的真实宽度没变——Tk 补不补、补多少都不是这里能控
       的。「该动的没被缓存挡掉」由第一遍写入值随 total 变化来证明；
       端到端的那条用真 geometry 验证回读值
       （``test_column_widths_catch_up_after_switching_back``）。
    """
    from bilibili_submit.ui import theme
    from bilibili_submit.ui.views.tasks_table import TaskTable, _column_widths

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
    written: list[int] = []
    real_column = table.tree.column

    def spy(column, *args, **kwargs):
        if column == "title" and "width" in kwargs:
            written.append(kwargs["width"])
        return real_column(column, *args, **kwargs)

    table.tree.column = spy  # type: ignore[method-assign]
    try:
        for _ in range(3):
            table.resize_columns()
    finally:
        table.tree.column = real_column  # type: ignore[method-assign]
        del table.tree.winfo_width

    # 三次重算，每轮写一遍七列——一遍都不写就是缓存把该动的也挡了
    assert len(written) == 3, f"宽度变了却写了 {len(written)} 次 title 列：{written}"
    # 写入值必须严格按 varying 出来的 total 走（减去交给 Tk 补的余量）
    expected = [
        _column_widths(
            900 + 100 * (turn + 1) - theme.TABLE_COLUMN_MARGIN
        )["title"]
        for turn in range(3)
    ]
    assert written == expected, f"写入门值没按新宽度算：{written} ≠ {expected}"


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


# ---------- 拖窗口：非当前页不许参与重排 ----------


def _app(root: tk.Tk):
    """建一个完整的 App（五页都在），窗口已经映射。"""
    from bilibili_submit.ui.app import App

    app = App(root)
    app.pack(fill="both", expand=True)
    root.geometry(f"{theme.DEFAULT_WIDTH}x{theme.DEFAULT_HEIGHT}")
    root.update_idletasks()
    root.update()
    return app


def _drag(root: tk.Tk, start: int, end: int, steps: int = 20) -> None:
    """连续改窗口尺寸，模拟一次拖拽。"""
    for step in range(1, steps + 1):
        width = round(start + (end - start) * step / steps)
        root.geometry(f"{width}x800+0+0")
        root.update()
        root.update_idletasks()


def _count_columns(tree) -> list[str]:
    """给 Treeview 的 ``column()`` 套一层计数器，返回调用记录列表。"""
    calls: list[str] = []
    real = tree.column
    tree.column = (  # type: ignore[method-assign]
        lambda column, *a, **kw: calls.append(column) or real(column, *a, **kw)
    )
    return calls


@needs_display
def test_only_the_current_page_stays_in_the_grid(root):
    """同一时刻只有一页在网格里，切页要换人。

    撤掉 ``grid_remove``（回到五页齐上、只靠 tkraise 分胜负）的话，
    窗口每变一次尺寸 Tk 就要给五页的控件全部重算几何——实测
    Xvfb 上 6.79ms/帧，投稿页 9.1ms/帧；真实机器上就是几十毫秒
    一帧，拖窗口发涩就是这么来的。数字见 tools/bench_resize.py。

    断言方向是「不在场」，所以窗口必须真的映射过：没映射的话五页
    全都是 ``ismapped() == False``，那会变成一条永远绿的假守卫。
    """
    app = _app(root)
    app.show("登录")
    root.update()

    assert app._views["登录"].winfo_ismapped(), "当前页必须真的在布局里"
    for key in ("投稿", "批量任务", "历史", "设置"):
        assert not app._views[key].winfo_ismapped(), (
            f"{key} 不在眼前却还在网格里——它会跟着每次 resize 重排一遍"
        )

    app.show("批量任务")
    root.update()
    assert app._views["批量任务"].winfo_ismapped()
    assert not app._views["登录"].winfo_ismapped(), "切走了就该退出布局"

    # 切回来还得能恢复：grid_remove 是记住配置的，grid_forget 会忘
    app.show("登录")
    root.update()
    assert app._views["登录"].winfo_ismapped(), "切回来的页必须重新入网格"


@needs_display
def test_offscreen_pages_do_not_relayout_while_resizing(root, tmp_path, monkeypatch):
    """拖窗口时，非当前页一次列宽都不许调。

    这条守的是**动作**而不是状态：上一条只说明那页不在网格里，这条
    说明它的控件真的没被 Tk 重新摆过。撤掉优化后，登录页拖一次窗口
    会让批量任务页和历史页的表格各调 20 次 ``tree.column()``——
    它们被盖着，改了也没人看见，纯粹是白干。

    数的是**调用次数**而不是耗时：耗时在 CI 上抖得厉害，次数是确定的。

    .. important::
       两页都必须**有内容**。空状态下列表容器整个是收起的，Treeview
       量到宽度 1，``resize_columns`` 会在一行之内早退——那样这条
       测试在「优化被撤掉」时也依然是绿的（第一版就踩了这个坑：
       变异注入后没红，还以为守住了）。
    """
    import json
    import time

    from conftest import run_until

    from bilibili_submit import scheduler

    app = _app(root)

    # 批量任务页：塞一个目录进去，让列表真的显示出来。状态文件也要
    # 隔离，否则残留目录会让这一页在后台重扫、往日志补行
    tasks = app._views["批量任务"]
    folder = tmp_path / "待投稿"
    folder.mkdir()
    for name in ("开场片段.mp4", "正片 · 上.mp4"):
        (folder / name).write_bytes(b"\x00\x00\x00\x00ftypmp4" + b"\x00" * 512)
    tasks._state_file = tmp_path / "ui-state.json"
    tasks._dir_var.set(str(folder))
    tasks._load()
    run_until(root, lambda: len(tasks._table.tree.get_children()) > 0)

    # 历史页：塞两条记录
    history_file = tmp_path / "history.json"
    history_file.write_text(
        json.dumps(
            [
                {"name": "任务一", "bvid": "BV1xx411c7mD", "time": time.time() - 60},
                {"name": "任务二", "bvid": "BV1yy411c7mD", "time": time.time()},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(scheduler, "DEFAULT_HISTORY_FILE", str(history_file))
    monkeypatch.setattr(
        "bilibili_submit.ui.views.history.DEFAULT_HISTORY_FILE", str(history_file)
    )
    history = app._views["历史"]
    history.refresh(force=True)
    root.update()
    # 这里**不能**用 winfo_ismapped()：非当前页的控件 ismapped 恒为
    # False（父容器不在网格里），那是优化在起作用，不是没数据。要确认
    # 「列表真的挂回了网格、树里真的有行」，用 winfo_manager() + 行数
    assert history._list_holder.winfo_manager() == "grid", (
        "历史页列表没挂回网格，这条测试会变成空跑"
    )
    assert len(history._tree.get_children()) == 2, "历史页该有两条记录"

    # 切到字段最少的登录页再拖：剩下四页都不该有反应
    app.show("登录")
    root.update()

    tasks_calls = _count_columns(tasks._table.tree)
    history_calls = _count_columns(history._tree)
    _drag(root, 1180, 950)

    assert tasks_calls == [], (
        f"登录页拖窗口时批量页写了 {len(tasks_calls)} 次列宽："
        "那页被盖着，重排它纯属白干"
    )
    assert history_calls == [], (
        f"登录页拖窗口时历史页写了 {len(history_calls)} 次列宽"
    )


@needs_display
def test_switching_pages_keeps_their_grid_config(root):
    """切走再切回，页面的 grid 配置一个字段都不许丢。

    用 ``grid_forget()`` 而不是 ``grid_remove()`` 就会踩这条：配置被
    忘掉之后，切回来的那一页既不拉伸也不占满，内容缩在左上角——
    而它**不报错**，只是看起来「界面坏了」。
    """
    app = _app(root)
    app.show("投稿")
    root.update()
    before = app._views["投稿"].grid_info()

    app.show("历史")
    root.update()
    app.show("投稿")
    root.update()
    after = app._views["投稿"].grid_info()

    for key in ("row", "column", "sticky", "in"):
        assert before[key] == after[key], (
            f"切回后 grid 配置变了：{key} 从 {before[key]!r} 变成 {after[key]!r}"
        )


@needs_display
def test_column_widths_catch_up_after_switching_back(root, tmp_path):
    """在别的页把窗口拉宽，切回批量页时列宽必须跟着新宽度。

    非当前页退出布局后，它的 ``<Configure>`` 也一起不来了。切回来时
    ``App._apply_layout`` 会因为「档位没变」而跳过 apply_layout 钩子，
    列宽全靠 ``grid()`` 恢复时 Tk 补发的那一次 Configure——这条路断
    的话，用户看到的就是「切了个页，表格列宽停在旧窗口的比例上」。

    列表得有内容才量得到宽度：空状态下整个列表容器是收起的，
    Treeview 会一直报宽 1。
    """
    from conftest import run_until

    from bilibili_submit.ui.views.tasks_table import COLUMNS, _column_widths

    app = _app(root)
    view = app._views["批量任务"]
    table = view._table

    # 造一个有两段视频的目录，让列表真的显示出来。状态文件也要隔离：
    # 否则真实目录残留会让这一页在构造期就开始异步重扫、往日志补行
    folder = tmp_path / "待投稿"
    folder.mkdir()
    for name in ("开场片段.mp4", "正片 · 上.mp4"):
        (folder / name).write_bytes(b"\x00\x00\x00\x00ftypmp4" + b"\x00" * 512)
    view._state_file = tmp_path / "ui-state.json"
    view._dir_var.set(str(folder))
    view._load()
    run_until(root, lambda: len(table.tree.get_children()) > 0)

    root.geometry(f"{layout.BREAKPOINT_WIDE + 200}x800+0+0")
    root.update_idletasks()
    root.update()

    app.show("历史")
    root.update()
    app.show("批量任务")
    root.update_idletasks()
    root.update()

    total = table.tree.winfo_width()
    assert total >= 100, f"表格没布局出来（宽 {total}）"
    # ttk Treeview 会把列宽总和钳到「控件宽 − 内缩量」（本页实测 2px，
    # 历史页 0px，随树而变），所以期望值按回读到的真实总和算；「跟上
    # 新窗口宽度」由总和本身盯着——它必须贴近 winfo_width，而不是停在
    # 旧窗口的比例上。
    actual_total = sum(table.tree.column(column, "width") for column in COLUMNS)
    assert total - 4 <= actual_total <= total, (
        f"列宽合计 {actual_total} 没贴近新窗口宽度 {total}"
    )
    for column, width in _column_widths(actual_total).items():
        assert table.tree.column(column, "width") == width, (
            f"{column} 列没跟上新窗口宽度：期望 {width}，"
            f"实际 {table.tree.column(column, 'width')}"
        )
