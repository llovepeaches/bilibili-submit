#!/usr/bin/env python3
"""图形界面打包入口。

为什么不用 ``main.py``：那是命令行入口，``argparse`` 的子命令是必填的
（``add_subparsers(required=True)``）。windowed exe 双击启动时没有任何参数，
走 CLI 解析会立刻因为「缺少子命令」退出（码 2），界面根本起不来。

所以 GUI 版单独一个入口，直接进界面。

命令行用户仍可用 ``bilibili-submit.exe gui`` 或 ``python run.py gui``，
那条路走 CLI 的 ``gui`` 子命令。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    from bilibili_submit.console import setup_console

    # windowed 进程没有 stdout，setup_console 会跳过切代码页。
    # 仍调用一次是为了给可能被重定向的输出加兜底。
    setup_console()

    from bilibili_submit.ui import gui_available, launch

    ok, reason = gui_available()
    if not ok:
        _show_error(f"无法启动界面：{reason}")
        return 1

    try:
        return launch()
    except Exception as exc:  # noqa: BLE001 - windowed 下必须兜住并弹窗
        _show_error(f"界面启动失败：{exc}")
        return 1


def _show_error(message: str) -> None:
    """窗口程序没有控制台，错误不弹出来用户永远看不到。"""
    try:
        import tkinter.messagebox as messagebox

        root = __import__("tkinter").Tk()
        root.withdraw()  # 只显示对话框，不要空窗口
        messagebox.showerror("哔哩哔哩自动投稿程序", message)
        root.destroy()
    except Exception:  # noqa: BLE001 - 连 Tk 都起不来就没办法了
        pass


if __name__ == "__main__":
    sys.exit(main())
