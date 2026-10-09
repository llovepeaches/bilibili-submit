"""界面测试的共享夹具。

这里的东西曾经在 6 个测试文件里各写一份。重复本身不是问题，
**改一处漏五处**才是：比如有人把 ``run_until`` 的 5 秒超时改成 0，
只有他改的那份会生效，其余五份继续用旧值，而没人会注意到。

放在 conftest 里还有个额外好处：pytest 会把本文件所在目录插到
``sys.path`` 最前面，所以测试文件可以 ``from conftest import ...``
直接取用，不需要把它变成一个可安装的包。

.. note::
   本目录**没有** ``__init__.py``。一旦加上，``import conftest`` 就得改
   成 ``from tests.conftest import ...``，pytest 的 prepend 导入模式
   不再把 tests/ 加到 sys.path。别顺手加那个文件。
"""

from __future__ import annotations

import os

import pytest

#: 正常的跳过预算。基线里本来就有 2 条（root 权限位、网络），
#: 所以阈值不能设成 0，但远超这个数就说明界面测试被整片跳过了。
_QUIET_SKIP_BUDGET = 5


def _display_available() -> bool:
    """真的试着建一个 Tk 根窗口。

    只看 ``DISPLAY`` 环境变量不够——很多环境里它被设了值，
    但 X server 根本不在（CI、容器里常见，本机休眠恢复后也是），
    那样 ``Tk()`` 才会暴露真相。
    """
    root = None
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        return True
    except Exception:  # noqa: BLE001 - 任何失败都当作没有显示环境
        return False
    finally:
        if root is not None:
            root.destroy()


def _require_display() -> bool:
    """``BILLI_REQUIRE_DISPLAY=1`` 时不许静默跳过，改成真跑真红。"""
    return os.environ.get("BILLI_REQUIRE_DISPLAY") == "1"


def _needs_display_marker(display_ok: bool, require: bool):
    """按「有没有屏」和「要不要强制」造 ``needs_display`` marker。

    严格模式下没有屏时**故意不跳过**：让测试真的去建窗口、真的抛
    TclError。跳过的测试不会告诉任何人「你改的界面代码一行都没验到」——
    红一次好过绿一辈子。
    """
    if require and not display_ok:
        return pytest.mark.skipif(False, reason="")
    return pytest.mark.skipif(
        not display_ok, reason="无显示环境（CI 可配 Xvfb 后自动启用）"
    )


needs_display = _needs_display_marker(_display_available(), _require_display())


def run_until(root, predicate, timeout_ms: int = 5000) -> None:
    """跑真实事件循环直到 ``predicate()`` 为真。

    为什么不用 ``update_idletasks()``：那只跑重绘，**不跑普通 ``after``
    回调**。Worker 完成只是把结果 ``after(0)`` 排进队列，能不能排上、
    什么时候排上都由事件循环说了算。

    超时直接 fail 而不是静默通过——宁可红一次，也不要让「异步结果没到达」
    被读成「功能正常」。
    """
    state = {"done": False}

    def poll():
        if predicate():
            state["done"] = True
            root.quit()
        else:
            root.after(10, poll)

    def on_timeout():
        root.quit()

    root.after(0, poll)
    root.after(timeout_ms, on_timeout)
    root.mainloop()
    assert state["done"], f"等待异步结果超时（{timeout_ms}ms）"
    assert predicate()


@pytest.fixture
def root():
    """一个 Tk 根窗口，用完销毁。

    标了 ``needs_display`` 的用例才能用它——没有 X server 时 ``Tk()``
    会直接抛 TclError。
    """
    import tkinter as tk

    made = tk.Tk()
    yield made
    made.destroy()


def environment_probe_targets() -> tuple[object, object]:
    """探测函数被打桩的位置（app 与 settings 各自导入了一份）。"""
    from bilibili_submit.ui import app as app_mod
    from bilibili_submit.ui.views import settings as settings_mod

    return app_mod, settings_mod


def pytest_terminal_summary(terminalreporter) -> None:
    """跳过数异常时喊一声。

    没挂 Xvfb 时界面测试是**静默**跳过的：测试报绿，改动却没被验证。
    这个横幅是唯一会提醒你的东西。
    """
    skipped = terminalreporter.stats.get("skipped", [])
    if len(skipped) <= _QUIET_SKIP_BUDGET:
        return

    terminalreporter.write_sep("!", "界面测试被整片跳过")
    terminalreporter.write_line(
        f"本次跳过了 {len(skipped)} 条用例，远超正常的 {_QUIET_SKIP_BUDGET} 条。"
    )
    terminalreporter.write_line(
        "多半是没有可用的 X server（DISPLAY 设了值不代表 X server 在跑）。"
    )
    terminalreporter.write_line("请改用:  xvfb-run -a python -m pytest tests/")
    terminalreporter.write_line(
        "想在 CI 里堵死这个口子:  BILLI_REQUIRE_DISPLAY=1（缺屏直接判失败）"
    )
    terminalreporter.write_sep("!", "")
