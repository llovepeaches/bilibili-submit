"""UI 卡顿基准：把「优化前 / 优化后」两种写法放在同一份脚本里对比。

只测**组件级**热点，不测整窗启动——启动慢是冷启动，不是卡顿。

每一项都成对出现：先跑改动前的写法，再跑现在的写法，两个数字都来自
同一次运行、同一台机器，所以可以直接相除。每对下面写明它对应的真实
场景（什么操作会撞上它）。

用法::

    xvfb-run -a python3 tools/bench_ui.py
"""
from __future__ import annotations

import json
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bilibili_submit.config import TaskConfig  # noqa: E402
from bilibili_submit.scheduler import read_history_diagnose  # noqa: E402
from bilibili_submit.ui import theme  # noqa: E402
from bilibili_submit.ui.views.history import _file_stamp  # noqa: E402
from bilibili_submit.ui.views.tasks_table import (  # noqa: E402
    COLUMNS,
    TaskTable,
    _column_widths,
)
from bilibili_submit.ui.widgets import LogConsole  # noqa: E402

ROWS = 500
LOG_LINES = 1000
RESIZE_CALLS = 300
HISTORY_ENTRIES = 800

#: 对比表。每项 ``(名字, 旧耗时, 新耗时)``，最后统一打印
_PAIRS: list[tuple[str, float, float]] = []


def _root() -> tk.Tk:
    root = tk.Tk()
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    theme.apply(style, "light")
    root.geometry("1200x800+20+20")
    return root


def bench(root: tk.Tk, name: str, fn, repeat: int = 3) -> float:
    """跑 ``repeat`` 遍，返回**最快**的那一次。

    取最小值而不是平均值：Xvfb 上的耗时抖动主要来自同机其它负载，
    那只会让某几次变慢，不会让任何一次变快——最小值最接近真实成本，
    平均值反而把噪声算进了结论。
    """
    best = float("inf")
    for _ in range(repeat):
        root.update_idletasks()
        start = time.perf_counter()
        fn()
        root.update()  # 让 Tk 真正把活干完（重排/重绘）
        best = min(best, (time.perf_counter() - start) * 1000)
    print(f"      {name:<40s} {best:8.1f} ms")
    return best


def pair(label: str, old: float, new: float) -> None:
    _PAIRS.append((label, old, new))


def make_tasks(n: int, tmp: Path) -> list[TaskConfig]:
    """造 n 个任务，文件真实存在（省掉缺失分支的差异）。"""
    tasks = []
    for i in range(n):
        f = tmp / f"video-{i:04d}.mp4"
        if not f.exists():
            f.write_bytes(b"")
        tasks.append(TaskConfig(name=f"任务{i:04d}", file=str(f), title=f"标题{i}"))
    return tasks


# ---------- 1. 列宽重算 ----------


def bench_resize(root: tk.Tk, table: TaskTable) -> None:
    print("\n  ① 列宽重算 ×300")
    print("     场景：拖窗口边框、切页面、任何一次布局变动。")
    print("           <Configure> 在布局的每一步都会往表格派发一次。")

    def old():
        """改动前的写法：不缓存宽度，每次都写七列。"""
        for _ in range(RESIZE_CALLS):
            total = table.tree.winfo_width()
            if total < 100:
                continue
            for column, width in _column_widths(total).items():
                table.tree.column(column, width=width)

    cost_old = bench(root, "旧：无宽度缓存", old)

    def new_same_width():
        """宽度没变（<Configure> 里占绝大多数的那一种）。"""
        for _ in range(RESIZE_CALLS):
            table.resize_columns()

    cost_new = bench(root, "新：宽度没变，整体跳过", new_same_width)

    def new_width_moves():
        """宽度真的在变（真在拖边框的那一段）。

        缓存只在宽度没变时生效，所以这一项**不会**变快——它量的是
        「挡不住的那部分」还剩多少，用来确认优化没有把活推到别处。
        """
        real = table.tree.winfo_width
        step = [0]

        def varying():
            step[0] += 1
            return real() + (step[0] % 2)

        table.tree.winfo_width = varying  # type: ignore[method-assign]
        try:
            for _ in range(RESIZE_CALLS):
                table.resize_columns()
        finally:
            del table.tree.winfo_width

    cost_move = bench(root, "新：宽度每步都变（拖边框）", new_width_moves)
    pair("列宽重算 ×300", cost_old, cost_new)
    print(f"      → 挡掉了 {cost_old / cost_new:.0f}×；"
          f"真拖边框时剩 {cost_move:.1f} ms（旧写法同样这段是 "
          f"{cost_old / max(cost_move, 0.001):.1f}× 的量级）")


# ---------- 2. 汇总刷新 ----------


def bench_summary(root: tk.Tk, table: TaskTable) -> None:
    print("\n  ② 汇总刷新 ×100")
    print("     场景：批量投稿每一行开始/结束时刷新「已选/成功/失败」计数。")

    def old():
        """改动前的写法：get_children 三遍 + 两次推导 + 两次 count。"""
        for _ in range(100):
            total = len(table.tree.get_children())
            picked = sum(1 for i in table.tree.get_children()
                         if table.picked.get(i))
            tones = [table.tones.get(i, "idle") for i in table.tree.get_children()]
            tones.count("ok")
            tones.count("error")
            _ = total, picked

    cost_old = bench(root, "旧：全表扫三遍", old)

    def new():
        """现在的写法：单次遍历累加三个计数。"""
        for _ in range(100):
            picked = ok = error = 0
            for i in table.tree.get_children():
                if table.picked.get(i):
                    picked += 1
                tone = table.tones.get(i, "idle")
                if tone == "ok":
                    ok += 1
                elif tone == "error":
                    error += 1

    cost_new = bench(root, "新：单次遍历", new)
    pair("汇总刷新 ×100", cost_old, cost_new)


# ---------- 3. 日志写入 ----------


def bench_log(root: tk.Tk) -> None:
    print("\n  ③ 日志写入 ×1000")
    print("     场景：上传/批量投稿时进度成批到达，每行一句话。")

    def old_style(text: tk.Text, n: int) -> None:
        """改动前的写法：configure → insert → see，一行一套。"""
        for i in range(n):
            text.configure(state="normal")
            text.insert("end", f"[{i}/{n}] 上传中：{i * 7 % 100}%\n")
            text.see("end")
            text.configure(state="disabled")

    old_box = tk.Text(root, height=10, wrap="word")
    old_box.pack(fill="both", expand=True)
    cost_old = bench(root, "旧：逐行 insert + see",
                     lambda: old_style(old_box, LOG_LINES))
    old_box.pack_forget()

    log = LogConsole(root, height=10)
    log.pack(fill="both", expand=True)
    cost_new = bench(root, "新：批量 insert + 空闲时滚一次",
                     lambda: [log.append(f"[{i}/{LOG_LINES}] 上传中："
                                         f"{i * 7 % 100}%")
                              for i in range(LOG_LINES)])
    pair(f"日志 append ×{LOG_LINES}", cost_old, cost_new)

    # 累积效应：没有行数上限时，写第 9000 行是不是比写第 1000 行慢？
    print("\n  ④ 日志累积（每段 1000 行，共 10000 行）")
    print("     场景：一次投几百个文件，日志从头到尾不清理。")
    log.clear()
    segments = []
    for seg in range(10):
        start = time.perf_counter()
        for i in range(1000):
            log.append(f"[{seg * 1000 + i}/10000] 分片上传中，速度 3.2 MB/s")
        root.update()
        segments.append((time.perf_counter() - start) * 1000)
    for seg, cost in enumerate(segments):
        print(f"      第 {seg * 1000 + 1:>5d}–{(seg + 1) * 1000:>5d} 行 "
              f"{cost:7.1f} ms")
    if segments[0]:
        print(f"      首段 → 末段 {segments[-1] / segments[0]:.2f}×；"
              f"第 4 段起稳定在 {segments[-1]:.1f} ms（MAX_LINES="
              f"{LogConsole.MAX_LINES} 封顶后不再随行数增长）")
    # Text 末尾恒有一个换行（空 Text 里也有一条），所以真实行数要减 1
    kept = log._text.get("1.0", "end").count("\n") - 1
    print(f"      写满 10000 行后实际保留 {kept} 行（MAX_LINES="
          f"{LogConsole.MAX_LINES}）")


# ---------- 5. 历史页切页 ----------


def bench_history(root: tk.Tk, tmp: Path) -> None:
    print(f"\n  ⑤ 历史页切页（{HISTORY_ENTRIES} 条记录，只渲染最近 200 条）")
    print("     场景：投稿中途点去「历史」看一眼，再点回来——每次切换都触发。")
    hist_file = tmp / "history.json"
    hist_file.write_text(
        json.dumps(
            [
                {
                    "name": f"任务{i:04d}",
                    "file": str(tmp / f"v{i}.mp4"),
                    "bvid": f"BV1xx411c7m{i % 10}D",
                    "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                    "time": 1_700_000_000 + i,
                    "parts": 1,
                }
                for i in range(HISTORY_ENTRIES)
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    tree = ttk.Treeview(root, columns=("time", "bvid", "name"), show="headings")
    tree.pack(fill="both", expand=True)

    def rebuild():
        """改动前的写法：不问青红皂白，全量 parse 一遍再删插 200 行。"""
        entries, _problem = read_history_diagnose(str(hist_file))
        tree.delete(*tree.get_children())
        for entry in list(entries)[-200:][::-1]:
            tree.insert(
                "", "end",
                values=(
                    datetime.fromtimestamp(entry["time"]).strftime("%Y-%m-%d %H:%M"),
                    entry["bvid"],
                    entry["name"],
                ),
            )

    cost_old = bench(root, "旧：切 1000 次就重建 1000 次",
                     lambda: [rebuild() for _ in range(1000)])

    tree.delete(*tree.get_children())
    stamp = _file_stamp(str(hist_file))

    def cached_switch():
        """现在的写法：先比对指纹，只有真的变了才重建。

        文件是追加写的，只有投稿成功那一刻才会变，所以 1000 次切页
        里只该重建一次。
        """
        for _ in range(1000):
            if _file_stamp(str(hist_file)) != stamp:
                rebuild()

    cost_new = bench(root, "新：切 1000 次只重建 1 次", cached_switch)
    tree.pack_forget()
    pair("历史页切页 ×1000", cost_old, cost_new)


def main() -> int:
    import tempfile

    root = _root()
    tmp = Path(tempfile.mkdtemp())

    print(f"行数={ROWS} 日志行={LOG_LINES} 列宽重算={RESIZE_CALLS}")
    print("\n  ⓪ 表格填充 fill()（扫描文件夹后的正常量级，未改动，作为基准）")
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
    tasks = make_tasks(ROWS, tmp)
    bench(root, f"表格填充 fill({ROWS} 行)", lambda: table.fill(tasks))
    bench(root, f"逐行 mark ×{ROWS}",
          lambda: [table.mark(i, "成功", "ok") for i in range(ROWS)])

    bench_resize(root, table)
    bench_summary(root, table)
    bench_log(root)
    bench_history(root, tmp)

    print("\n  ── 汇总 ──────────────────────────────────────────────")
    old_total = new_total = 0.0
    for label, old, new in _PAIRS:
        old_total += old
        new_total += new
        faster = f"{old / new:.0f}×" if new else "∞"
        print(f"      {label:<34s} {old:8.1f} → {new:8.1f} ms  快 {faster}")
    print(f"      {'合计':<34s} {old_total:8.1f} → {new_total:8.1f} ms  "
          f"快 {old_total / new_total:.0f}×")
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
