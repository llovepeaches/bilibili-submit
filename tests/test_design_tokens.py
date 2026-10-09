"""设计令牌守卫：把 2026-10 第二次改版敲定的决策钉成硬断言。

这些断言大半和 ``test_theme_contrast.py`` / ``test_ui.py`` 里的相邻——
不合并过去是刻意的：那份改版有四条**和用户确认过的决策**（粉底墨字
主按钮、中性色保粉相、不做自绘标题栏、五页全重做），把它们的数值
集中放在一个文件里，将来谁想「顺手调一下颜色」时能在一处看清
每条约束背后的来龙去脉，而不是在两个大文件里大海捞针。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bilibili_submit.ui import theme  # noqa: E402
from test_theme_contrast import contrast  # noqa: E402


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_primary_button_is_pink_with_ink_text(mode):
    """主按钮是**粉底墨字**，不是设计稿原样的粉底白字。

    设计稿的 ``#FB7299`` 底 + 白字只有 2.64:1，WCAG AA（4.5:1）都够不着；
    换成 ``#241A1F`` 墨字后浅色 6.41:1。这条是四条决策里唯一主动
    偏离设计稿的地方，改回白字这里先红。
    """
    theme.set_mode(mode)
    assert theme.INK_ON_PINK == "#241A1F"
    ratio = contrast(theme.PINK, theme.INK_ON_PINK)
    assert ratio >= 4.5, (
        f"{mode}: 主按钮 {theme.PINK} 底 {theme.INK_ON_PINK} 字 "
        f"只有 {ratio:.2f}:1，低于 AA"
    )


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_thumb_gradient_follows_theme(mode):
    """缩略图占位渐变三色随主题走，且上下限不是同一个颜色。

    渐变是纯装饰、上面不压文字，不参与文本对比度；但**深浅两套必须
    各有一份**——深色模式里亮一片浅色粉比不画还刺眼。
    """
    light = theme.palette_for("light")
    dark = theme.palette_for("dark")
    for key in ("THUMB_FROM", "THUMB_MID", "THUMB_TO"):
        assert light[key] != dark[key], f"{key} 深浅两套写成了同一个值"
    theme.set_mode(mode)
    assert theme.THUMB_FROM and theme.THUMB_MID and theme.THUMB_TO


def test_status_bar_is_top_of_window_geometry():
    """状态条 30px 且是**顶部**布局的几何前提。

    App 的网格把状态条放在 row0、主体在 row1——这个结构由
    ``test_ui.py`` 的布局用例盯着；这里只钉数值本身：设计稿画布
    900px 里状态条固定 30px，压缩空间时先压内容区不动它。
    """
    assert theme.STATUS_BAR_H == 30


def test_segmented_control_and_chip_use_control_metrics():
    """分段控件、标签 chips 与标准控件共用一套度量。

    分段控件高 = :data:`theme.CONTROL_HEIGHT`（36px）、chip 高 24px、
    圆角一律 7px 小圆角（药丸圆角只留给状态点/徽标）。散写在这些
    组件里但都从 theme 取——这里断言的是「theme 的值没被人顺手改掉」。
    """
    from bilibili_submit.ui import widgets  # noqa: F401  触导入即校验常量

    assert theme.CONTROL_HEIGHT == 36
    assert theme.RADIUS_CONTROL == 7
    assert widgets.TagChipField.CHIP_H == 24
    assert widgets.SegmentedControl._PAD == 3
