"""拖窗口单帧归因：Python 回调 vs Tk 自身重排。

bench_resize.py 告诉我们**每帧 7~9ms**，但没说是谁花的。这里用
cProfile 抓连续拖窗口期间的 Python 侧调用，回答两个问题：

1. 我们的 Configure 处理器（``_on_root_configure`` / ``resize_columns`` /
   ``_on_canvas_configure`` / ``_relayout`` …）占多少？
2. 剩下的开销在 Tk 的 C 层（重排 + 重绘），那唯一能做的优化就是
   **减少参与布局的控件数量**——尤其是那四页被盖住、却仍在
   grid 里占位的视图。

用法::

    xvfb-run -a python3 tools/profile_resize.py
"""

from __future__ import annotations

import cProfile
import io
import pstats
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.bench_resize import PAGES, build, drag, pump  # noqa: E402


def profile_page(app, root, page: str) -> None:
    app.show(page)
    pump(root, 200)
    root.geometry("1180x800+0+0")
    pump(root, 60)

    profiler = cProfile.Profile()
    profiler.enable()
    frames = drag(root, 1180, 950, 30)
    profiler.disable()

    total = sum(frames)
    stream = io.StringIO()
    pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(28)
    lines = stream.getvalue().splitlines()
    print(f"\n=== {page}  30 帧共 {total:.0f}ms（均值 {total / 30:.1f}ms） ===")
    for line in lines:
        if line.startswith("   Ordered by") or line.startswith("List reduced"):
            continue
        print(line)


def main() -> int:
    root, app = build("light")
    try:
        for page in PAGES:
            profile_page(app, root, page)
    finally:
        root.destroy()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
