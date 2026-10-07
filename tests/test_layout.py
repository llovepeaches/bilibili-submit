"""布局档位测试。

档位划分是纯算术，所以这个文件的用例**全部不需要 Tk**——
建窗口拖尺寸来测断点，只会得到一个既慢又脆的测试。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.ui import layout, theme  # noqa: E402


# ---------- 档位边界 ----------


@pytest.mark.parametrize(
    "width",
    [
        (theme.MIN_WIDTH, layout.COMPACT),
        (899, layout.COMPACT),
        (900, layout.MEDIUM),
        (theme.DEFAULT_WIDTH, layout.MEDIUM),
        (1279, layout.MEDIUM),
        (1280, layout.WIDE),
        (1920, layout.WIDE),
        (9999, layout.WIDE),
    ],
)
def test_tier_boundaries(width):
    """门槛是「左闭右开」：900 属于 medium，1280 属于 wide。"""
    value, expect = width
    assert layout.tier_for(value) == expect, f"{value}px 应当是 {expect}"


def test_breakpoint_is_above_min_width():
    """断点必须高于最小窗口宽度，否则最低档永远触发不到。

    这是「断点写成死代码」的唯一防线：MIN_WIDTH 和 BREAKPOINT_MEDIUM
    分在两个文件里，谁改小了另一个都不会报错，界面只是「好像没变化」。
    """
    assert layout.BREAKPOINT_MEDIUM > theme.MIN_WIDTH, (
        f"断点 {layout.BREAKPOINT_MEDIUM} 不高于最小宽度 {theme.MIN_WIDTH}，"
        "compact 档永远触发不到"
    )
    assert layout.tier_for(theme.MIN_WIDTH) == layout.COMPACT


def test_every_tier_is_reachable():
    """三档都得有真实宽度能落到它头上——挡位是给人用的，不是给表看的。"""
    reached = {layout.tier_for(width) for width in range(theme.MIN_WIDTH, 2001)}
    assert reached == {layout.COMPACT, layout.MEDIUM, layout.WIDE}


# ---------- 每档参数 ----------


def test_compact_collapses_the_sidebar():
    """只有最窄那档收侧栏，其余两档保持展开。"""
    assert layout.layout_for(880).nav_collapsed is True
    assert layout.layout_for(1180).nav_collapsed is False
    assert layout.layout_for(1920).nav_collapsed is False


def test_collapsed_sidebar_is_narrow_but_clickable():
    """图标栏要窄，但不能窄到汉字图标放不下。

    导航图标是一个汉字（约 28px 宽），56px 里左右各留 12px 正好居中。
    低于 48 就会开始切边。
    """
    spec = layout.layout_for(880)
    assert spec.nav_width == theme.NAV_WIDTH_COLLAPSED
    assert 48 <= spec.nav_width <= 72, f"图标栏 {spec.nav_width}px 不合理"
    assert spec.nav_width < layout.layout_for(1180).nav_width


def test_only_wide_uses_two_columns():
    """双列只给最宽档：中等宽度下每列不到 500px，表单说明文字会换行。"""
    assert layout.layout_for(880).columns == 1
    assert layout.layout_for(1180).columns == 1
    assert layout.layout_for(1920).columns == 2
    assert layout.layout_for(1920).gutter > 0


def test_list_min_height_grows_with_the_tier():
    """窗口越高，任务列表至少能显示的行数越多。"""
    heights = [
        layout.layout_for(880).list_min_height,
        layout.layout_for(1180).list_min_height,
        layout.layout_for(1920).list_min_height,
    ]
    assert heights == sorted(heights), f"列表最小高度没有随档位递增：{heights}"
    assert all(height >= theme.ROW_HEIGHT * 3 for height in heights), (
        "最小高度要装得下三行，否则列表看着像空的"
    )


def test_layout_values_are_grid_aligned():
    """档位里的尺寸都落在栅格上——不然间距会「差不多但不齐」。"""
    half = theme.GRID // 2
    for width in (880, 1180, 1920):
        spec = layout.layout_for(width)
        for name in ("nav_width", "gutter", "list_min_height", "pad_page"):
            assert getattr(spec, name) % half == 0, f"{name} 没对齐栅格"


# ---------- 通用列宽 ----------


_COLUMNS = ("pick", "name", "file")
_WEIGHTS = {"pick": 0, "name": 3, "file": 5}
_MINIMUMS = {"pick": 34, "name": 70, "file": 110}


def test_column_widths_splits_by_weight():
    widths = layout.column_widths(_COLUMNS, _WEIGHTS, _MINIMUMS, 1000)
    assert widths["file"] > widths["name"], "权重大的列应当更宽"


def test_column_widths_never_goes_below_the_minimum():
    for total in (0, 100, 400, 1000, 4000):
        widths = layout.column_widths(_COLUMNS, _WEIGHTS, _MINIMUMS, total)
        for column in _COLUMNS:
            assert widths[column] >= _MINIMUMS[column], (
                f"{column} 在 {total}px 下窄于下限"
            )


def test_column_widths_keeps_zero_weight_columns_fixed():
    """权重为 0 的列不跟着窗口变宽——勾选列始终 34px 才不会漂移。"""
    assert layout.column_widths(_COLUMNS, _WEIGHTS, _MINIMUMS, 600)["pick"] == 34
    assert layout.column_widths(_COLUMNS, _WEIGHTS, _MINIMUMS, 4000)["pick"] == 34


def test_column_widths_grows_with_the_window():
    narrow = layout.column_widths(_COLUMNS, _WEIGHTS, _MINIMUMS, 600)
    wide = layout.column_widths(_COLUMNS, _WEIGHTS, _MINIMUMS, 1400)
    assert wide["name"] > narrow["name"]
    assert wide["file"] > narrow["file"]
