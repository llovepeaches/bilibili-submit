"""拖窗口（放大缩小）卡顿基准。

和 ``tools/bench_ui.py`` 分工：那个测**组件级**热点（列宽、日志、汇总），
这个测**整窗拖拽**——用户说的是「客户端放大缩小很卡」，那就要把
「连续改窗口尺寸」这件事本身量化出来。

测什么::

    for 每一帧:
        root.geometry(f"{w}x{h}")
        t0 = perf_counter()
        root.update()          # 让 Tk 把这一帧的 Configure 链全跑完
        dt = perf_counter() - t0

三个维度分开报，因为它们回答的是三个不同的问题：

1. **按页面**——五页都叠在同一个 grid 单元格里，Tk 在尺寸变化时会
   给每一页的控件都重算几何。哪一页最贵，拖的时候就在哪一页卡。
2. **同档内拖 / 跨档拖**——同档内（``App._on_root_configure`` 直接
   return）只剩 Tk 自己的重排；跨档会额外触发 ``apply_layout``
   （侧栏折叠、设置页双列、列表 minsize），那是另一笔成本。
3. **帧耗时分布**——均值说明平均体验，P95 和最大值说明「卡顿感」
   来自哪几帧。拖窗口时的顿挫几乎全是长尾造成的。

用法::

    xvfb-run -a python3 tools/bench_resize.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from bilibili_submit.ui import app as app_mod  # noqa: E402
from bilibili_submit.ui import theme  # noqa: E402

#: 演示数据。和 tools/shoot_docs.py 同一套，保证「测的就是用户看到的那一页」
DEMO_VIDEOS = ("开场片段.mp4", "正片 · 上.mp4", "正片 · 下.mp4")

#: 五页。按导航顺序，报的时候也按这个顺序
PAGES = ("登录", "投稿", "批量任务", "历史", "设置")

#: 同档内拖：1180→950，全程落在 medium 档（900≤w<1280）
SAME_TIER = (1180, 950, 30)
#: 跨档拖：1280→880，一路穿过 wide→medium→compact 两道断点
CROSS_TIER = (1280, 880, 40)
#: 反向跨档：880→1280（compact→medium→wide）
GROW_TIER = (880, 1280, 40)

#: 帧耗时超过这个数就算「能感觉出来的一帧」
FRAME_BUDGET_MS = 16.7


def pump(root: tk.Tk, ms: int = 350) -> None:
    """让主线程真跑一小段事件循环（异步探测靠 after 排队的）。"""
    root.update_idletasks()
    root.update()
    root.after(ms, root.quit)
    root.mainloop()


def make_demo_dir(base: Path) -> Path:
    folder = base / "待投稿"
    folder.mkdir(parents=True, exist_ok=True)
    for name in DEMO_VIDEOS:
        (folder / name).write_bytes(b"\x00\x00\x00\x00ftypmp4" + b"\x00" * 512)
    return folder


def make_demo_history(path: Path) -> None:
    now = time.time()
    entries = [
        {"time": now - 3600 * 26, "bvid": "BV1Gx411c7mD", "name": "开场片段"},
        {"time": now - 3600 * 3, "bvid": "BV1yX4y1K7qA", "name": "正片 · 上"},
    ]
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def build(mode: str = "light"):
    """起一个填好演示数据的窗口，返回 ``(root, app)``。"""
    root = tk.Tk()
    root.title("bench")
    root.geometry("1180x800+0+0")
    root.configure(background=theme.BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    theme.apply(style, mode)
    app_mod._configure_nav_style(style)

    app = app_mod.App(root)
    app.pack(fill="both", expand=True)

    tmp = Path(tempfile.mkdtemp(prefix="bench-resize-"))
    demo_dir = make_demo_dir(tmp)
    make_demo_history(tmp / "history.json")

    # 批量任务页：塞一个目录进去，让表格真有 3 行
    tasks = app._views["批量任务"]
    tasks._state_file = tmp / "ui-state.json"
    tasks._dir_var.set(str(demo_dir))
    tasks._load()

    # 历史页：塞两条记录
    from bilibili_submit.ui.views import history as history_mod

    history_mod.DEFAULT_HISTORY_FILE = str(tmp / "history.json")
    app._views["历史"].refresh(force=True)

    # 投稿页：填一个文件 + 标题，把预检区点亮（它字段最多）
    upload = app._views["投稿"]
    upload._file_var.set(str(demo_dir / DEMO_VIDEOS[0]))
    upload._title_var.set("【4K】开场片段 · 完整版")

    pump(root, 600)
    return root, app


def drag(root: tk.Tk, start: int, end: int, steps: int) -> list[float]:
    """从 ``start`` 拖到 ``end``，返回每一帧的耗时（毫秒）。"""
    frames: list[float] = []
    for step in range(1, steps + 1):
        width = round(start + (end - start) * step / steps)
        # 高度跟着按比例动一点：真实拖窗口很少只改一个方向
        height = 800 + (step % 5 - 2) * 8
        root.geometry(f"{width}x{height}+0+0")
        start_t = time.perf_counter()
        root.update()
        root.update_idletasks()
        frames.append((time.perf_counter() - start_t) * 1000)
    return frames


def stats(frames: list[float]) -> dict:
    ordered = sorted(frames)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    return {
        "frames": len(frames),
        "total_ms": round(sum(frames), 1),
        "mean_ms": round(sum(frames) / len(frames), 2),
        "p95_ms": round(p95, 2),
        "max_ms": round(max(frames), 2),
        "over_budget": sum(1 for f in frames if f > FRAME_BUDGET_MS),
    }


def main() -> int:
    root, app = build("light")
    rows: list[dict] = []
    try:
        for page in PAGES:
            app.show(page)
            pump(root, 250)
            for label, (start, end, steps) in (
                ("同档内 1180→950", SAME_TIER),
                ("跨档 1280→880", CROSS_TIER),
                ("反向 880→1280", GROW_TIER),
            ):
                # 拖完先回到起点，否则第二次拖的长度对不上
                root.geometry(f"{start}x800+0+0")
                pump(root, 60)
                frames = drag(root, start, end, steps)
                rows.append({"page": page, "case": label, **stats(frames)})
    finally:
        root.destroy()

    # 汇总表
    header = f"{'页面':<8}{'场景':<18}{'帧数':>5}{'均值':>9}{'P95':>9}{'最大':>9}{'超16.7ms':>10}"
    print(header)
    print("-" * len(header))
    for row in rows:
        print(
            f"{row['page']:<8}{row['case']:<18}{row['frames']:>5}"
            f"{row['mean_ms']:>8.2f}{row['p95_ms']:>9.2f}"
            f"{row['max_ms']:>9.2f}{row['over_budget']:>10}"
        )

    worst = max(rows, key=lambda r: r["p95_ms"])
    total_frames = sum(r["frames"] for r in rows)
    over = sum(r["over_budget"] for r in rows)
    print()
    print(f"最卡的组合：{worst['page']} · {worst['case']}  P95={worst['p95_ms']}ms")
    print(f"全场景共 {total_frames} 帧，其中 {over} 帧超过 {FRAME_BUDGET_MS}ms")
    print()
    print("（帧耗时 = geometry() 之后一次 root.update() 的成本；")
    print("  60fps 的预算是 16.7ms/帧，超了就会被看出来）")

    out = ROOT / "bench-resize.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细写进 {out}")
    return 0


if __name__ == "__main__":
    if not shutil.which("Xvfb") and not sys.platform.startswith(("win", "darwin")):
        print("没有 X server；用 xvfb-run -a python3 tools/bench_resize.py 跑")
        raise SystemExit(1)
    raise SystemExit(main())
