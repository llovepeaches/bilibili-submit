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
    assert theme.STATUS_BAR_H == theme.scale_px(30)


def test_segmented_control_and_chip_use_control_metrics():
    """分段控件、标签 chips 与标准控件共用一套度量。

    分段控件高 = :data:`theme.CONTROL_HEIGHT`（36px）、chip 高 24px、
    圆角一律 7px 小圆角（药丸圆角只留给状态点/徽标）。散写在这些
    组件里但都从 theme 取——这里断言的是「theme 的值没被人顺手改掉」。
    """
    from bilibili_submit.ui import widgets  # noqa: F401  触导入即校验常量

    assert theme.CONTROL_HEIGHT == theme.scale_px(36)
    assert theme.RADIUS_CONTROL == theme.scale_px(7)
    assert widgets.TagChipField.CHIP_H == theme.scale_px(24)
    assert widgets.SegmentedControl._PAD == theme.scale_px(3)


def test_table_column_margin_does_not_scale():
    """列宽余量是 Tk 主题的内缩量，不随界面缩放变。

    缩放因子只放大我们自己的像素常量；Tk 在 Xvfb/96 DPI 下给 treeview
    的内缩量（列宽总和与控件宽之间的差）始终是 2px。把这个值也乘上
    UI_SCALE 的话，高分屏下余量会大于真实内缩，Tk 的 stretch 就要
    多补几个 px——列宽总和虽然仍能对齐，但补的方式是按列均摊，
    权重比例会被打乱。
    """
    assert theme.TABLE_COLUMN_MARGIN == 2


# ---------- 可读性专项（2026-10） ----------


def test_font_roles_floor_keeps_chinese_legible():
    """最小字阶不低于 caption 10pt（≈13.3px）。

    中文是方块音节文字，12px（9pt）下笔画密度过高；Tk 在 Windows 上
    走 GDI 文本、不开 ClearType，同字号比原生 DirectWrite 控件又虚一
    档。两个因素叠起来，9pt 的说明文字就是「看得见、看不清」。这条
    断言钉住下限：谁要把 caption 调回 9，这里先红。
    """
    assert theme.FONT_ROLES["caption"][0] >= 10
    assert min(size for size, _bold, _mono in theme.FONT_ROLES.values()) >= 10


def test_font_roles_keep_their_ladder():
    """级差不能塌：display > title > body ≥ caption，且加粗档不比常规档小。

    层级靠「字号 + 字重 + 颜色」三个维度叠加，字号那一维自己乱掉的
    话，剩下两维补不回来（Tk 只有 normal/bold 两档）。
    """
    roles = theme.FONT_ROLES
    assert roles["display"][0] > roles["title"][0] > roles["subtitle"][0]
    assert roles["body"][0] >= roles["caption"][0]
    assert roles["body-strong"][0] == roles["body"][0], "加粗不放大字号"
    assert roles["body-strong"][1] is True


def test_apply_ui_scale_rederives_every_metric(monkeypatch):
    """改缩放偏好要把**所有**派生常量整套重算，不是只改几个。

    控件尺寸是从这些模块级常量读走的，漏一个的后果是 150% 下大部分
    东西放大了、某一个还是 96 DPI 的尺寸——那种不一致比不缩放更难查。
    """
    from bilibili_submit.ui import layout as ui_layout

    baseline = {
        name: getattr(theme, name)
        for name in (
            "GRID", "PAD_XS", "PAD_SM", "PAD_MD", "PAD_LG", "PAD_XL",
            "RADIUS_CONTROL", "RADIUS_CARD", "CONTROL_HEIGHT",
            "PRIMARY_BUTTON_HEIGHT", "STATUS_BAR_H", "THUMB_W", "THUMB_H",
            "NAV_WIDTH_EXPANDED", "NAV_WIDTH_COLLAPSED", "NAV_ITEM_HEIGHT",
            "ROW_HEIGHT", "MIN_WIDTH", "MIN_HEIGHT", "DEFAULT_WIDTH",
        )
    }
    try:
        factor = theme.apply_ui_scale("1.5")
        assert factor == 1.5
        for name, value in baseline.items():
            assert getattr(theme, name) > value, f"{name} 没有跟着缩放放大"
        # 旧名 tuple 也要跟上：Canvas 自绘文字传的是元组不是字体名
        # （body 11pt × 1.5 = 16.5 → Tk 取整 16）
        assert theme.FONT_NORMAL[1] == 16
        # 不缩放的量保持不动
        assert theme.TABLE_COLUMN_MARGIN == 2
        assert theme.LOG_HEIGHT == 7

        # layout 的断点与档位表是 import 时算的，必须能重算
        ui_layout.rebuild()
        assert ui_layout.BREAKPOINT_MEDIUM == theme.scale_px(900)
        assert ui_layout.BREAKPOINT_WIDE == theme.scale_px(1280)
        assert ui_layout.layout_for(900).nav_width == theme.NAV_WIDTH_COLLAPSED
    finally:
        theme.apply_ui_scale("auto")
        ui_layout.rebuild()
    assert theme.GRID == baseline["GRID"], "还原后不能留下放大后的值"


def test_ui_scale_preference_falls_back_to_detection(monkeypatch):
    """auto 与脏值都走系统检测；环境变量优先于偏好（排查不能被打断）。"""
    from bilibili_submit.ui import layout as ui_layout

    monkeypatch.delenv("BILI_UI_SCALE", raising=False)
    try:
        assert theme.apply_ui_scale("auto") == 1.0, "Xvfb 96 DPI 恒为 1.0"
        assert theme.apply_ui_scale("完全不认识的值") == 1.0
        assert theme.apply_ui_scale("1.25") == 1.25

        monkeypatch.setenv("BILI_UI_SCALE", "2.0")
        assert theme.apply_ui_scale("1.25") == 2.0, "环境变量是排查手段，优先"
    finally:
        monkeypatch.delenv("BILI_UI_SCALE", raising=False)
        theme.apply_ui_scale("auto")
        ui_layout.rebuild()


def test_scale_choices_cover_the_common_dpi_cases():
    """三档手动缩放覆盖常见诉求：标准 / 稍大 / 大，外加 auto。

    档位不在 theme 里写死文案（那是 state 的职责），这里只钉「选项
    集合本身」——设置页下拉框和偏好文件解析都读它。
    """
    assert theme.UI_SCALE_CHOICES == ("auto", "1.0", "1.25", "1.5")
    from bilibili_submit.ui import state

    assert state.APP_UI_SCALES == theme.UI_SCALE_CHOICES
    assert set(state.UI_SCALE_LABELS) == set(theme.UI_SCALE_CHOICES)


def test_resolve_ui_scale_reads_percent_as_a_factor():
    """"125%" 是 1.25 倍，不是 125 倍——不除一百会被上限钳成 3.0。"""
    assert theme._resolve_ui_scale("125%") == 1.25
    assert theme._resolve_ui_scale("1.5") == 1.5
    assert theme._resolve_ui_scale(1.25) == 1.25
    # 超出范围的钳到 0.5~3.0，不抛
    assert theme._resolve_ui_scale("900%") == 3.0
    assert theme._resolve_ui_scale("0.1") == 0.5
