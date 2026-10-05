"""配色对比度测试：每个前景/底色组合都要过 WCAG。

为什么值得单独一个文件：色板是``theme.py`` 里的字面量，改色不会有
任何报错——把``#9E9E9E`` 换成更浅的灰，界面只是「变淡了一点」，
没有任何东西会告诉你它已经不达标了。这里把它变成会红的断言。

阈值（WCAG 2.1）：
- 正文4.5:1（AA）
- 大字（18px+/14px bold）、UI 组件、图形、**焦点环** 3:1
"""

import math
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.ui import theme  # noqa: E402

#: WCAG 相对亮度公式里的三通道权重
_LUMA = (0.2126, 0.7152, 0.0722)
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _relative_luminance(color: str) -> float:
    """sRGB 相对亮度。

    注意要先**线性化**：直接拿 0-255 的通道值算会得到偏高的亮度，
    浅色一侧尤其不准（会把及格的颜色误判成不达标）。
    """
    assert _HEX.match(color), f"{color} 不是 #RRGGBB"
    channels = []
    for index in range(1, 7, 2):
        raw = int(color[index:index + 2], 16) / 255.0
        channels.append(
            raw / 12.92 if raw <= 0.04045 else ((raw + 0.055) / 1.055) ** 2.4
        )
    return sum(weight * value for weight, value in zip(_LUMA, channels))


def _oklch(color: str) -> tuple[float, float, float]:
    """sRGB 十六进制 → OKLCH 的 ``(明度L, 彩度C, 色相H度)``。

    用来回答一个对比度回答不了的问题：两个颜色**是不是同一种颜色**。

    对比度只看「差多少」，色相看「往哪偏」。把``#6B5A62``（粉灰）
    换成``#6B5B5B``（褐灰），对比度几乎不变，肉眼却一眼看出脏了——
    因为后者色相 +18°，朝着橙褐去了。彩度C 两者都是 0.02 左右，
    **光看彩度抓不住这类错误，必须看色相。**
    """
    assert _HEX.match(color), f"{color} 不是 #RRGGBB"
    channels = []
    for index in range(1, 7, 2):
        raw = int(color[index:index + 2], 16) / 255.0
        channels.append(
            raw / 12.92 if raw <= 0.04045 else ((raw + 0.055) / 1.055) ** 2.4
        )
    long_, medium, short = (
        0.4122214708 * channels[0] + 0.5363325363 * channels[1] + 0.0514459929 * channels[2],
        0.2119034982 * channels[0] + 0.6806995451 * channels[1] + 0.1073969566 * channels[2],
        0.0883024619 * channels[0] + 0.2817188376 * channels[1] + 0.6299787005 * channels[2],
    )
    cube = [max(value, 0.0) ** (1 / 3) for value in (long_, medium, short)]
    lightness = 0.2104542553 * cube[0] + 0.7936177850 * cube[1] - 0.0040720468 * cube[2]
    green = 1.9779984951 * cube[0] - 2.4285922050 * cube[1] + 0.4505937099 * cube[2]
    blue = 0.0259040371 * cube[0] + 0.7827717662 * cube[1] - 0.8086757660 * cube[2]
    return lightness, math.hypot(green, blue), math.degrees(math.atan2(blue, green))


# 品牌粉的色相带（OKLCH，负值 = 偏红一侧）。B 站粉 ``#FB7299`` 落在 -12 度附近。
# 留了 20 度左右的余量，因为不同明度上「看起来还是粉」的色相会轻微漂移
# （实测色板在 -5 ~ -25 度之间），但任何一个中性色跑出这个带子就是掉色相了。
_BRAND_HUE_RANGE = (-32.0, 4.0)


def contrast(fg: str, bg: str) -> float:
    """WCAG 对比度：``(较亮 + 0.05) / (较暗 + 0.05)``。"""
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


#: 每个模式下要检查的组合。顺序无关紧要，失败信息里会带标签。
#:
#: 这里的底色覆盖了所有真实出现过的表面——只测``PAPER`` 会漏掉
#: 「文字压在导航底上」这类实际很常见的组合。
_PAIRS = [
    ("正文 / 卡片", "INK", "PAPER", 4.5),
    ("正文 / 窗口底", "INK", "SHELL", 4.5),
    ("正文 / 次级区", "INK", "PAPER_ALT", 4.5),
    ("次级文字 / 卡片", "INK_SECOND", "PAPER", 4.5),
    ("弱化文字 / 卡片", "INK_MUTED", "PAPER", 4.5),
    ("弱化文字 / 窗口底", "INK_MUTED", "SHELL", 4.5),
    ("弱化文字 / 导航底", "INK_MUTED", "NAV_BG", 4.5),
    ("主按钮 粉底墨字", "INK_ON_PINK", "PINK", 4.5),
    ("主按钮按下 粉底墨字", "INK_ON_PINK", "PINK_PRESSED", 4.5),
    ("导航选中文字 / 选中底", "NAV_ACTIVE_FG", "NAV_ACTIVE_BG", 4.5),
    ("导航文字 / 导航底", "NAV_FG", "NAV_BG", 4.5),
    ("操作条文字 / 墨底", "INK_SURFACE_FG", "INK_SURFACE", 4.5),
    ("操作条弱化 / 墨底", "INK_SURFACE_MUTED", "INK_SURFACE", 4.5),
    ("日志文字 / 日志底", "LOG_TEXT", "LOG_BG", 4.5),
    ("折叠标题 / 折叠底", "INK", "COLLAPSE_BG", 4.5),
    ("折叠弱化 / 折叠底", "INK_MUTED", "COLLAPSE_BG", 4.5),
    ("输入框文字 / 输入框", "INK", "FIELD_BG", 4.5),
    ("深梅文字 / 卡片", "PINK_DEEP", "PAPER", 4.5),
    ("深梅文字 / 粉浅底", "PINK_DEEP", "PINK_TINT", 4.5),
    # 焦点环是 UI 图形，3:1 即可——但它是最常被漏掉的一条
    ("焦点环 / 卡片", "FOCUS_RING", "PAPER", 3.0),
    ("焦点环 / 窗口底", "FOCUS_RING", "SHELL", 3.0),
    ("焦点环 / 输入框", "FOCUS_RING", "FIELD_BG", 3.0),
    # 禁用态在 WCAG 里可豁免，但本应用的「未登录时禁用主按钮」
    # 需要用户读懂原因，所以按 3:1 硬要求
    ("禁用文字 / 禁用底", "DISABLED_FG", "DISABLED_BG", 3.0),
]


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_palette_contrast_meets_wcag(mode):
    """两套色板的每个组合都要达标。"""
    palette = theme.palette_for(mode)
    failures = [
        f"{label} = {contrast(palette[fg], palette[bg]):.2f}:1 < {minimum}"
        for label, fg, bg, minimum in _PAIRS
        if contrast(palette[fg], palette[bg]) < minimum
    ]
    assert not failures, f"{mode} 色板不达标：\n  " + "\n  ".join(failures)


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_semantic_tones_meet_wcag(mode):
    """状态色（标签/列表行）前景对浅底要过 AA。

    状态色是最容易被随手调的一组——「让绿色更醒目一点」很容易
    顺手调到 3:1 以下，而它们承载的是成败信息。
    """
    theme.set_mode(mode)
    try:
        failures = [
            f"{name} = {contrast(fg, bg):.2f}:1 < 4.5"
            for name, (fg, bg) in theme.TONES.items()
            if contrast(fg, bg) < 4.5
        ]
    finally:
        theme.set_mode("light")
    assert not failures, f"{mode} 语义色不达标：\n  " + "\n  ".join(failures)


def test_accent_button_passes_on_its_own_color():
    """主按钮文字必须用墨色，不能用白色。

    B 站粉 ``#FB7299`` 配白字只有 2.64:1 —— 这是本项目最容易犯的
    一个错（因为「粉底白字」是下意识的选择），所以单独立一条测试。
    """
    ratio = contrast(theme.INK_ON_PINK, theme.PINK)
    assert ratio >= 4.5, (
        f"主按钮文字 {theme.INK_ON_PINK} 压在 {theme.PINK} 上只有 {ratio:.2f}:1"
    )
    assert theme.INK_ON_PINK != "#FFFFFF", "粉底配白字是2.64:1，不达标"


def test_focus_ring_is_not_the_accent_color():
    """焦点环用深梅而不是粉——粉在浅底上只有 2.64:1，看不见。"""
    palette = theme.palette_for("light")
    assert palette["FOCUS_RING"] != palette["PINK"]
    assert contrast(palette["FOCUS_RING"], palette["PAPER"]) >= 3.0


def test_muted_text_is_not_numeric_gray():
    """弱化文字和禁用文字都必须带品牌色相，且不能用数字化中性灰。

    ``#9E9E9E``（Windows 默认的次要文字灰）在白底上只有 2.68:1。
    数字化的中性灰是「AI 味」和「不可读」的共同来源。

    判据是**色相**而不是「红通道是否高于蓝通道」——后者会被``#6B5B5B``
    这种褐灰骗过去：它的 R=107 > B=91 成立，但色相 +18° 已经跑到橙褐那边，
    看着就是脏。彩度也不够用，粉灰 0.025 vs 褐灰 0.021 几乎分不出。
    """
    # 禁用文字同样要查：它也承载信息（这个按钮为什么点不动），
    # 而且是用户唯一能看到「原因被截断了」的地方——变成褐灰就丢了
    for name in ("INK_MUTED", "DISABLED_FG", "INK_SECOND"):
        for mode in ("light", "dark"):
            value = theme.palette_for(mode)[name]
            _, chroma, hue = _oklch(value)
            low, high = _BRAND_HUE_RANGE
            assert chroma > 0.004, f"{mode} {name}={value} 几乎是纯灰（彩度 {chroma:.4f}）"
            assert low <= hue <= high, (
                f"{mode} {name}={value} 掉了品牌色相：色相 {hue:.1f}° "
                f"不在品牌粉色带 {low}° ~ {high}° 内"
            )


def test_neutral_colors_carry_a_hint_of_brand_hue():
    """中性色不是纯灰：全部带一点粉相（OKLCH 彩度 ≥ 0.003）。

    这是一条主观但可测的设计约定：用户说不出为什么，只会觉得
    「这界面是哔哩的」。允许极低彩度，但不允许零。
    """
    neutral = ("SHELL", "PAPER", "PAPER_ALT", "LINE", "LINE_STRONG", "INK", "NAV_BG")
    for mode in ("light", "dark"):
        palette = theme.palette_for(mode)
        for name in neutral:
            value = palette[name]
            _, chroma, hue = _oklch(value)
            low, high = _BRAND_HUE_RANGE
            assert chroma > 0.002, f"{mode} {name}={value} 是纯灰，没有品牌色相"
            assert low <= hue <= high, (
                f"{mode} {name}={value} 跑出品牌粉色带：色相 {hue:.1f}°"
            )


def test_ink_tones_readable_on_both_ink_surfaces():
    """墨色块（操作条/状态栏）上的状态色要够亮。

    浅色主题的墨底是 ``#241A1F``，深色主题的墨底是 ``#2E2429``——
    两个模式下墨色块的颜色不一样（深色下要「更亮」而不是更深），
    所以两组都得过。
    """
    for mode in ("light", "dark"):
        surface = theme.palette_for(mode)["INK_SURFACE"]
        for name, value in theme._INK_TONES.items():
            ratio = contrast(value, surface)
            assert ratio >= 4.5, f"[{mode}] 墨底上的 {name} 只有 {ratio:.2f}:1"


def test_ink_tones_differ_from_ordinary_tones_in_light_mode():
    """墨底状态色不能直接抄浅底状态色。

    浅色模式下两者的底色差得最远（纸白 vs 深梅墨），如果这时候前景
    还一样，就说明墨底根本没适配过——压上去必然糊。
    深色模式下两者碰巧一致是正常的（深色浅底本来就深，两种底都
    吃亮色），所以这里只断言浅色模式。
    """
    theme.set_mode("light")
    try:
        for name in ("ok", "warn", "error"):
            assert theme.tone(name)[0] != theme.tone_on_ink(name), (
                f"浅色模式的墨底状态色 {name} 和浅底一样，是漏适配不是巧合同色"
            )
    finally:
        theme.set_mode("light")


def test_every_tone_has_a_distinct_glyph():
    """状态不能只靠颜色传达——每个语义色都要有专属形状记号。

    色盲用户看不出「粉色 = 进行中」。双通道编码（颜色 + 形状）才
    是真的可访问。
    """
    glyphs = set(theme.TONE_GLYPHS.values())
    assert set(theme.TONES) <= set(theme.TONE_GLYPHS), "有语义色缺glyph"
    assert len(glyphs) == len(theme.TONES), f"glyph 有重复：{theme.TONE_GLYPHS}"
