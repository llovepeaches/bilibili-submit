"""界面层的分层守卫。

这些用例不测行为，测的是**依赖方向**。它们存在的理由很具体：

``ui/`` 一旦能随手 import 业务层，界面就会慢慢变成第二个业务层——
「顺手在这里算一下分区号」「顺手在这里发一次请求」，然后这些逻辑
既没有单测也没有第二处调用点，只能靠手点界面验证。等到想拆的时候，
每一根这样的线都得先想清楚能不能剪。

**为什么用 AST 而不是搜字符串**：正则会把注释里写的「这里不要直接调
``upload_video``」也当成一次违规调用，反过来也会把
``from .upload import ...``（``ui/views/upload.py``，界面自己的模块）
误判成 import 了业务层的 ``bilibili_submit.upload``。

**为什么是「此刻为真」**：守卫不是愿望清单。写下一条今天就会红的
守卫，唯一的后果是下一次有人顺手把它放宽——那时它就不再是守卫了。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, NamedTuple

import pytest

UI_ROOT = Path(__file__).resolve().parents[1] / "bilibili_submit" / "ui"
#: 包根（``bilibili_submit`` 所在的目录）。算绝对模块名必须先剥掉它——
#: 用绝对路径拼出来的 ``/workspace/bilibili_submit.config`` 永远匹配不上
#: 任何规则，守卫会一路假绿到有人真的往组件层塞业务依赖为止。
PKG_ROOT = UI_ROOT.parents[1]

#: 业务层模块。界面可以拿它们的数据结构，但不该成为流程的一部分
BUSINESS = {
    "auth",
    "client",
    "config",
    "upload",
    "multipart",
    "scheduler",
    "submit",
    "exceptions",
    "metadata",
    "ffmpeg",
    "update",
}

#: 纯组件层：只负责画控件和排版，不该认识任何业务概念
PURE_UI = ["widgets.py", "theme.py", "layout.py", "qr.py", "workers.py"]

#: 纯类型模块：里面**只有类型标注**，运行期不定义任何东西。
#: 视图在 ``if TYPE_CHECKING`` 下引它们，标注能写准，运行期零依赖——
#: 这是「标注不求值」（:mod:`__future__`）之外，另一种不给运行期添麻烦的方式。
_PURE_TYPE_MODULES = {"_host.py"}

#: ``views/tasks.py`` 在 ``if TYPE_CHECKING`` 下允许引入的名字。
#: 键是名字，值是「它必须来自哪个纯类型模块」。
TYPED_ONLY = {"TaskOutcome", "AppHost"}


class ImportRef(NamedTuple):
    """一次 import 的解析结果。"""

    module: str  # 绝对模块名，如 bilibili_submit.scheduler
    line: int
    names: tuple[str, ...]
    typed: bool  # 是否只在 if TYPE_CHECKING 块里


def _resolve(node: ast.ImportFrom, rel: Path) -> str:
    """把相对 import 还原成绝对模块名。

    ``bilibili_submit/ui/views/tasks.py`` 里的 ``from ...config import ...``：
    所在目录是 ``bilibili_submit/ui/views``，``level=3`` 表示往上退两级
    （level-1），也就是包根，拼上 module 得到 ``bilibili_submit.config``。

    ``rel`` 必须是**相对包根**的路径，否则拼出来的是一串文件系统路径。
    """
    if not node.level:
        # 绝对 import，没什么要还原的
        return node.module or ""
    directory = rel.parent
    for _ in range(node.level - 1):
        directory = directory.parent
    package = ".".join(directory.parts)
    return f"{package}.{node.module}" if node.module else package


def _type_checking_lines(tree: ast.Module) -> set[int]:
    """``if TYPE_CHECKING:`` 块里那些 import 的行号。

    它们只喂给类型检查器，运行期不执行；配合文件顶部那句
    ``from __future__ import annotations``，连标注本身都不求值。
    所以「运行期耦合」要把它们排除在外，但纯组件层那条守卫不排除——
    那里连类型都不该知道业务。
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if isinstance(test, ast.Name):
            name = test.id
        elif isinstance(test, ast.Attribute):
            name = test.attr
        else:
            continue
        if name != "TYPE_CHECKING":
            continue
        for sub in ast.walk(node):
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                lines.add(sub.lineno)
    return lines


def _imports(path: Path) -> Iterator[ImportRef]:
    """列出文件里所有指向 ``bilibili_submit.*`` 的 import。

    第三方库与本模块的相对互引（``ui/`` 内部）都不在守卫范围——
    后者是同一个界面层内部的组织方式，不是跨层依赖。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    typed = _type_checking_lines(tree)
    rel = path.relative_to(PKG_ROOT)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _resolve(node, rel)
            names = tuple(a.name for a in node.names)
            yield ImportRef(module, node.lineno, names, node.lineno in typed)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield ImportRef(
                    alias.name, node.lineno, (alias.name,), node.lineno in typed
                )


def _is_business(module: str) -> bool:
    """``bilibili_submit.<业务模块>`` 才算数。

    注意 ``bilibili_submit.ui.views.upload`` 不算：那是界面自己的模块，
    只是恰好也叫 upload。
    """
    parts = module.split(".")
    return len(parts) >= 2 and parts[0] == "bilibili_submit" and parts[1] in BUSINESS


def _show(path: Path, line: int) -> str:
    return f"{path.relative_to(PKG_ROOT)}:{line}"


# ---------- 守卫 1：纯组件层 ----------


@pytest.mark.parametrize("name", PURE_UI)
def test_pure_widgets_know_nothing_about_business(name):
    """``widgets`` / ``theme`` / ``layout`` / ``qr`` / ``workers`` 只画控件。

    它们一旦认识业务，就再也没法单独实例化：想测一个按钮的配色，
    得先造出一个 AppConfig。反过来也成立——它们干净的时候，
    测它们连窗口都不用建。
    """
    path = UI_ROOT / name
    assert path.exists(), f"{name} 不在了——守卫指向的文件消失等于守卫自动失效"

    bad = [ref for ref in _imports(path) if _is_business(ref.module)]
    assert not bad, (
        f"{name} 是纯组件，不该依赖业务层，但 import 了：\n"
        + "\n".join(f"  {_show(path, ref.line)}  {ref.module}" for ref in bad)
        + "\n\n组件需要业务数据时，把它作为参数传进来，而不是自己 import。"
    )


# ---------- 守卫 2：界面不直接发投稿 ----------


def test_ui_never_reaches_for_upload():
    """界面层不许直接碰 ``upload`` 模块或 ``upload_video``。

    投稿是怎么发出去的（重试、分片、错误归类）是 ``scheduler`` 的事。
    界面自己调一次 ``upload_video``，等于把这套逻辑在界面里复制一份
    还不带重试——而且没有任何单测会覆盖到那条路径。
    """
    bad_imports: list[str] = []
    bad_names: list[str] = []

    for path in sorted(UI_ROOT.rglob("*.py")):
        for ref in _imports(path):
            if ref.module == "bilibili_submit.upload" and not ref.typed:
                bad_imports.append(f"{_show(path, ref.line)}  {ref.module}")

        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            hit = False
            if isinstance(node, ast.Name):
                hit = node.id == "upload_video"
            elif isinstance(node, ast.Attribute):
                hit = node.attr == "upload_video"
            if hit:
                bad_names.append(f"{_show(path, node.lineno)}  upload_video")

    assert not bad_imports and not bad_names, (
        "界面层不该直接调上传：\n"
        + "\n".join(f"  {item}" for item in bad_imports + bad_names)
        + "\n\n要投稿请走 scheduler.run_task——那里才有重试和错误归类。"
    )


# ---------- 守卫 3：TasksView 的棘轮 ----------


def test_tasks_view_keeps_business_at_arms_length():
    """棘轮：``views/tasks.py`` 运行期不再 import ``scheduler`` / ``submit``。

    Step 3~5 把标题编辑、表格、批量执行三块先后搬走之后，本文件只剩
    装配和生命周期钩子。这条守卫锁住的就是这个结果：它不许再长回去。

    ``scheduler`` 的 ``TaskOutcome`` 是个例外——它只出现在类型标注里，
    放在 ``if TYPE_CHECKING`` 下 import，运行期不产生依赖。但连它也
    被钉死在「只允许纯类型」上：想借类型标注之名再引一个业务符号，
    这条会先红。

    ``views/_host.py`` 的 ``AppHost`` 也在这个例外名单里，而且比
    ``TaskOutcome`` 更干净：它是一个 :class:`~typing.Protocol`，运行期
    什么也不导入、什么也不定义。所以这份白名单放行的判据是**「纯类型」**
    而不是「恰好只有这几个名字」——名单可以更新，但放行的前提是对
    ``_is_business`` 之外的模块、且只出现在标注里。
    """
    path = UI_ROOT / "views" / "tasks.py"
    assert path.exists()

    runtime = [ref for ref in _imports(path) if not ref.typed and _is_business(ref.module)]
    runtime = [ref for ref in runtime if ref.module.split(".")[1] in {"scheduler", "submit"}]
    assert not runtime, (
        "views/tasks.py 运行期不该 import scheduler / submit，但：\n"
        + "\n".join(f"  {_show(path, ref.line)}  {ref.module}" for ref in runtime)
        + "\n\nTasksView 只做装配。要调业务入口，放进 views/tasks_run.py。"
    )

    typed_refs = [ref for ref in _imports(path) if ref.typed]
    typed_names = {name for ref in typed_refs for name in ref.names}
    assert typed_names <= TYPED_ONLY, (
        f"views/tasks.py 在 TYPE_CHECKING 下 import 了 {sorted(typed_names)}，"
        f"目前只允许 {sorted(TYPED_ONLY)}（纯类型标注用）。\n\n"
        "这里的每个名字都必须来自 UI 层自己的纯类型模块；"
        "从业务层借一个符号进来（哪怕只用在标注里），这条会先红。"
    )
    # 白名单不只是几个名字：它还钉住「这些名字来自纯类型模块」。
    # 没有这一步，往白名单里加一个业务符号就能合法通过——
    # 白名单会退化成一张许可名单，而不是一条判据。
    for ref in typed_refs:
        if ref.module.split(".")[-1] not in _PURE_TYPE_MODULES:
            continue
        assert not _is_business(ref.module), (
            f"{ref.module} 被列为纯类型模块，但它其实在业务层名单里。"
        )
