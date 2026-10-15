"""重拍文档截图（docs/images 下那 18 张）。

用法::

    xvfb-run -a python3 tools/shoot_docs.py

.. important::
   必须在 **Xvfb** 下跑：``import -window root`` 截的是整个 X 根窗口，
   没有 X server 就没有东西可截。

两处关键约束：

1. **主题必须在建界面之前定下来**。tk/ttk 把颜色写进控件，事后改色板
   不会回填到已建好的控件上（设置页里那句「要重启客户端」说的就是这
   件事）。所以这里靠替换 ``load_app_state`` 决定模式，而不是建完再切。
2. **必须 ``pump()``**。异步探测（登录二维码、ffmpeg、环境自检）是靠
   ``after`` 排到主线程事件循环里的，只调 ``update()`` 它们一帧都跑不
   到——截出来就是一块还没画完的空界面。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "docs" / "images"
#: 和 Xvfb 默认屏幕一致。``import -window root`` 截的是整个屏幕，窗口
#: 比它小就会在四周留一圈黑边——最小尺寸那张尤其明显。所以常规截图
#: 就按屏幕尺寸开窗口，最小尺寸那张改成截窗口本体（见 :func:`shoot`）。
WIDTH, HEIGHT = 1280, 1024

#: 截图用的演示数据。放在这里而不是脚本外，是为了让「截图里那条记录」
#: 和「截图里那个任务」永远对得上——换机器也能拍出一样的图。
DEMO_VIDEOS = ("开场片段.mp4", "正片 · 上.mp4", "正片 · 下.mp4")


def pump(root, ms: int = 350) -> None:
    """让主线程真的跑一小段事件循环。"""
    root.update_idletasks()
    root.update()
    root.after(ms, root.quit)
    root.mainloop()


def shoot(root, name: str, crop_to_window: bool = False) -> None:
    """截一张图存进 ``docs/images``。

    默认 ``import -window root`` 截整个屏幕——窗口铺满屏幕时这最简单，
    也不用去查窗口 id。

    ``crop_to_window=True`` 时改成截**窗口本体**：窗口比屏幕小（最小
    尺寸那张）时整屏截图会在四周留一圈黑边，看着像没截好。Tk 在 X11
    上拿到的 ``winfo_id()`` 就是 X 的 window id，ImageMagick 认它。
    """
    pump(root)
    target = OUT / f"{name}.png"
    window = hex(root.winfo_id()) if crop_to_window else "root"
    subprocess.run(
        ["import", "-window", window, str(target)],
        check=True,
        cwd=str(ROOT),
    )
    print(f"  → {target.name} ({_size(target)})")


def _size(path: Path) -> str:
    from PIL import Image

    with Image.open(path) as image:
        return f"{image.width}x{image.height}"


def make_demo_dir(base: Path) -> Path:
    """造一个有几段视频的演示目录（批量任务页用）。"""
    folder = base / "待投稿"
    folder.mkdir(parents=True, exist_ok=True)
    for name in DEMO_VIDEOS:
        (folder / name).write_bytes(b"\x00\x00\x00\x00ftypmp4" + b"\x00" * 512)
    return folder


def make_demo_history(path: Path) -> None:
    """造一份投稿历史（历史页用）。"""
    now = time.time()
    entries = [
        {"time": now - 3600 * 26, "bvid": "BV1Gx411c7mD", "name": "开场片段"},
        {"time": now - 3600 * 3, "bvid": "BV1yX4y1K7qA", "name": "正片 · 上"},
    ]
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def build(mode: str, width: int = WIDTH, height: int = HEIGHT):
    """按给定主题模式起一个窗口。

    主题走 ``theme.apply(style, mode)`` 而不是改偏好文件：偏好那条路要
    先落盘再被 :func:`~.ui.state.resolve_theme_mode` 读回来，而截图脚本
    不该去动用户真实的配置文件。
    """
    import tkinter as tk
    from tkinter import ttk

    from bilibili_submit.ui import app as app_mod
    from bilibili_submit.ui import theme

    root = tk.Tk()
    root.title("哔哩哔哩自动投稿程序")
    root.geometry(f"{width}x{height}+0+0")
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
    pump(root, 500)
    return root, app


def main() -> int:
    import shutil
    import tempfile

    if not shutil.which("import"):
        print("缺 ImageMagick 的 import 命令，装一下：apt install imagemagick")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="shoot-"))
    demo_dir = make_demo_dir(tmp)
    make_demo_history(tmp / "history.json")

    for mode in ("light", "dark"):
        print(f"[{mode}]")
        root, app = build(mode)

        # 01 登录
        app.show("登录")
        shoot(root, f"{mode}-01-login")

        # 02 投稿（默认：还没选文件）
        app.show("投稿")
        shoot(root, f"{mode}-02-upload")

        # 03 批量任务（带演示目录）
        tasks = app._views["批量任务"]
        tasks._state_file = tmp / "ui-state.json"
        tasks._dir_var.set(str(demo_dir))
        tasks._load()
        pump(root, 900)
        app.show("批量任务")
        shoot(root, f"{mode}-03-tasks")

        # 04 历史（带演示记录）
        from bilibili_submit.ui.views import history as history_mod

        history_mod.DEFAULT_HISTORY_FILE = str(tmp / "history.json")
        app._views["历史"].refresh(force=True)
        app.show("历史")
        shoot(root, f"{mode}-04-history")

        # 05 设置
        app.show("设置")
        shoot(root, f"{mode}-05-settings")

        # ``logged_in`` 是只读的（由 cookie 文件算出来），所以靠换 cookie
        # 路径来切登录态，而不是给属性赋值
        missing_cookie = tmp / "no-such-cookie.json"
        good_cookie = tmp / "cookie.json"
        good_cookie.write_text(
            json.dumps({"SESSDATA": "demo", "bili_jct": "demo", "DedeUserID": "1"}),
            encoding="utf-8",
        )

        upload = app._views["投稿"]
        # 06 投稿页 · 条件没齐（未登录）
        app.ctx.cookie_file = str(missing_cookie)
        app.show("投稿")
        upload._file_var.set("")
        upload._refresh_action_bar()
        shoot(root, f"{mode}-06-upload-blocked")

        # 07 投稿页 · 就绪
        app.ctx.cookie_file = str(good_cookie)
        upload._file_var.set(str(demo_dir / DEMO_VIDEOS[0]))
        upload._title_var.set("【4K】开场片段 · 完整版")
        upload._refresh_action_bar()
        shoot(root, f"{mode}-07-upload-ready")

        # 08 投稿页 · 运行中
        upload._set_busy(True)
        shoot(root, f"{mode}-08-upload-busy")
        upload._set_busy(False)

        root.destroy()

        # 09 最小尺寸
        from bilibili_submit.ui import theme

        root, app = build(mode, theme.MIN_WIDTH, theme.MIN_HEIGHT)
        app.show("投稿")
        shoot(root, f"{mode}-09-min-size", crop_to_window=True)
        root.destroy()

    shutil.rmtree(tmp, ignore_errors=True)
    print("完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
