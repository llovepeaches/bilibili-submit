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


# 宽度一律相对断点取：门槛本身随 theme.UI_SCALE 缩放，写死 900/1280
# 在非默认缩放下会全部落错档位。这里测的是「左闭右开」的边界语义。
@pytest.mark.parametrize(
    "width",
    [
        (theme.MIN_WIDTH, layout.COMPACT),
        (layout.BREAKPOINT_MEDIUM - 1, layout.COMPACT),
        (layout.BREAKPOINT_MEDIUM, layout.MEDIUM),
        (theme.DEFAULT_WIDTH, layout.MEDIUM),
        (layout.BREAKPOINT_WIDE - 1, layout.MEDIUM),
        (layout.BREAKPOINT_WIDE, layout.WIDE),
        (layout.BREAKPOINT_WIDE + 640, layout.WIDE),
        (9999, layout.WIDE),
    ],
)
def test_tier_boundaries(width):
    """门槛是「左闭右开」：medium 档属于 medium，wide 档属于 wide。"""
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
    # 上限从断点派生：断点随 UI_SCALE 缩放，写死 2000 的话 150% 以上
    # 的界面里 wide 门槛已经高过探测范围，三档就只剩两档
    upper = layout.BREAKPOINT_WIDE + 200
    reached = {
        layout.tier_for(width)
        for width in range(theme.MIN_WIDTH, upper + 1)
    }
    assert reached == {layout.COMPACT, layout.MEDIUM, layout.WIDE}


# ---------- 每档参数 ----------


def test_compact_collapses_the_sidebar():
    """只有最窄那档收侧栏，其余两档保持展开。"""
    assert layout.layout_for(theme.MIN_WIDTH).nav_collapsed is True
    assert layout.layout_for(theme.DEFAULT_WIDTH).nav_collapsed is False
    assert layout.layout_for(layout.BREAKPOINT_WIDE).nav_collapsed is False


def test_collapsed_sidebar_is_narrow_but_clickable():
    """图标栏要窄，但不能窄到汉字图标放不下。

    导航图标是一个汉字（约 28px 宽），56px 里左右各留 12px 正好居中。
    低于 48 就会开始切边。
    """
    spec = layout.layout_for(theme.MIN_WIDTH)
    assert spec.nav_width == theme.NAV_WIDTH_COLLAPSED
    assert 48 * theme.UI_SCALE <= spec.nav_width <= 72 * theme.UI_SCALE, (
        f"图标栏 {spec.nav_width}px 不合理"
    )
    assert spec.nav_width < layout.layout_for(theme.DEFAULT_WIDTH).nav_width


def test_only_wide_uses_two_columns():
    """双列只给最宽档：中等宽度下每列不到 500px，表单说明文字会换行。"""
    assert layout.layout_for(theme.MIN_WIDTH).columns == 1
    assert layout.layout_for(theme.DEFAULT_WIDTH).columns == 1
    # 从断点派生而不是写死 1920：断点随 UI_SCALE 缩放，200% 下 wide
    # 门槛已经是 2560，写死的 1920 反而只算 medium
    wide = layout.BREAKPOINT_WIDE + 400
    assert layout.layout_for(wide).columns == 2
    assert layout.layout_for(wide).gutter > 0


def test_list_min_height_grows_with_the_tier():
    """窗口越高，任务列表至少能显示的行数越多。"""
    # 每档取一个代表宽度，从断点/Min_WIDTH 派生而不是写死 880/1180/1920：
    # 断点随 UI_SCALE 缩放，写死的值在 200% 下会挤进同一档
    widths = (
        theme.MIN_WIDTH,
        layout.BREAKPOINT_MEDIUM + 100,
        layout.BREAKPOINT_WIDE + 100,
    )
    heights = [layout.layout_for(width).list_min_height for width in widths]
    assert heights == sorted(heights), f"列表最小高度没有随档位递增：{heights}"
    assert all(height >= theme.ROW_HEIGHT * 3 for height in heights), (
        "最小高度要装得下三行，否则列表看着像空的"
    )


def test_layout_values_are_grid_aligned():
    """档位里的尺寸都落在栅格上——不然间距会「差不多但不齐」。"""
    half = theme.GRID // 2
    widths = (
        theme.MIN_WIDTH,
        layout.BREAKPOINT_MEDIUM + 100,
        layout.BREAKPOINT_WIDE + 100,
    )
    for width in widths:
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
