"""布局档位：窗口宽度 → 布局参数。

这一层回答一个 tkinter 自己不回答的问题：**窗口多宽才算窄**。
ttk 只有「按权重拉伸」，没有断点——表现是侧栏永远占 200px，
窗口压到 880 时内容区只剩 656px，而导航栏那 200px 里大半是空白。

档位划分是**纯算术**，所以这里刻意不 import tkinter：绑死在 Tk 上
就只能靠「建窗口 + 拖尺寸」来测，那种测试既慢又脆——理由和
``tasks._column_widths`` 抽成纯函数一样。

用法（:class:`App` 里）::

    spec = layout.layout_for(root.winfo_width())
    spec.nav_width        # 侧栏该多宽
    spec.nav_collapsed    # 是不是该收成图标栏
    spec.columns          # 内容卡片列数

.. important::
   断点门槛必须 **大于** :data:`~.theme.MIN_WIDTH`，否则最低那档
   永远触发不到，断点就成了死代码——有测试盯着这条
   （``tests/test_layout.py::test_breakpoint_above_min_width``）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from . import theme

__all__ = [
    "COMPACT",
    "MEDIUM",
    "WIDE",
    "BREAKPOINT_MEDIUM",
    "BREAKPOINT_WIDE",
    "Layout",
    "tier_for",
    "layout_for",
    "column_widths",
]

COMPACT = "compact"
MEDIUM = "medium"
WIDE = "wide"

#: 档位门槛，作用在**窗口总宽**（含侧栏）。
#:
#: 取值不是随手定的：``MIN_WIDTH`` 是 880，所以最低档的下界就在 880；
#: :data:`~.theme.DEFAULT_WIDTH` 是 1180，默认落在 medium；wide 给到
#: 1280 是因为设置页双列后每列约 520px——再窄 ``FormRow`` 的
#: ``wraplength=420`` 就要开始换行了。
BREAKPOINT_MEDIUM = 900
BREAKPOINT_WIDE = 1280


def tier_for(width: int) -> str:
    """窗口宽度 → 档位名。

    负数或 0（Tk 未映射时会给 1）一律按最窄档处理——调用方应当先自己
    挡掉 ``width < 100``，这里不再猜一次，免得两处判断不一致。
    """
    if width < BREAKPOINT_MEDIUM:
        return COMPACT
    if width < BREAKPOINT_WIDE:
        return MEDIUM
    return WIDE


@dataclass(frozen=True)
class Layout:
    """某个档位下的布局参数。

    全是**几何量**，不含任何业务逻辑。视图拿到它只许做
    ``grid_configure`` / ``configure(width=)`` / ``minsize`` 这一类事，
    不许顺手刷新数据——拖一次窗口就重扫一遍目录是这类钩子最容易
    犯的错。
    """

    tier: str
    #: 侧栏宽度
    nav_width: int
    #: 侧栏是否收成图标栏（只留汉字图标，隐藏文字与品牌名）
    nav_collapsed: bool
    #: 内容卡片列数。目前只有设置页用得上；批量任务页的主体是
    #: 7 列 Treeview，双列会把文件名压得认不出来
    columns: int
    #: 双列时两列之间的沟槽
    gutter: int
    #: 任务列表的最小高度。放在滚动区里时 ``weight=1`` 不生效
    #: （canvas 给 body 的是自然高度），只有 ``minsize`` 管用
    list_min_height: int
    #: 内容区四周的页面级留白
    pad_page: int


_LAYOUTS: dict[str, Layout] = {
    COMPACT: Layout(
        tier=COMPACT,
        nav_width=theme.NAV_WIDTH_COLLAPSED,
        nav_collapsed=True,
        columns=1,
        gutter=0,
        list_min_height=200,
        pad_page=theme.PAD_SM,
    ),
    MEDIUM: Layout(
        tier=MEDIUM,
        nav_width=theme.NAV_WIDTH_EXPANDED,
        nav_collapsed=False,
        columns=1,
        gutter=0,
        list_min_height=280,
        pad_page=theme.PAD_MD,
    ),
    WIDE: Layout(
        tier=WIDE,
        nav_width=theme.NAV_WIDTH_EXPANDED,
        nav_collapsed=False,
        columns=2,
        gutter=theme.PAD_LG,
        list_min_height=360,
        pad_page=theme.PAD_LG,
    ),
}


def layout_for(width: int) -> Layout:
    """窗口宽度 → 该档位的布局参数。"""
    return _LAYOUTS[tier_for(width)]


def column_widths(
    columns: Sequence[str],
    weights: Mapping[str, int],
    minimums: Mapping[str, int],
    total: int,
) -> dict[str, int]:
    """按权重把总宽度分给各列，返回每列宽度。

    抽成通用函数是因为**列表列宽不该两套算法**：批量任务页按权重分、
    历史页写死 140/130/240，结果是同一个窗口里两个表格的拉伸手感
    不一样，宽屏上历史页右侧还留一大片空白。

    ``weights`` 里为 0 的列不吃权重，固定拿 ``minimums`` 的值
    （勾选列就该始终 34px，不该跟着窗口变宽）。

    Args:
        columns: 列名顺序。
        weights: 列宽权重，0 表示固定列。
        minimums: 每列的下限。权重再小也不能窄到看不清内容。
        total: 可用总宽度。
    """
    fixed = sum(minimums[column] for column in columns if not weights[column])
    weight_sum = sum(weights.values())
    usable = max(total - fixed, 0)
    return {
        column: (
            minimums[column]
            if not weights[column]
            else max(
                minimums[column],
                int(usable * weights[column] / weight_sum),
            )
        )
        for column in columns
    }
