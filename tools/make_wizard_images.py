#!/usr/bin/env python3
"""生成安装向导用的两张 PNG 资产。

Inno Setup 现行版的 ``WizardImageFile`` / ``WizardSmallImageFile``
收 PNG（6.5.2 起）与真 alpha 透明；而老指令
``WizardSmallBitmapImage`` 在 Inno Setup 6 早期就更名，现行版本
直接不认——ISCC 报 Unrecognized directive 中断编译。BMP 色键透明
也早已不再支持（只剩 32-bit BMP 的 alpha 通道一条路），24-bit BMP
的品红角落会原样印在向导上。所以向导图统一走 PNG。

产物（源稿是同名的 .bmp，留在仓库里当设计稿）：

- ``assets/wizard-sidebar.png``  480×918 侧栏渐变。164:314 是
  ``WizardImageFile`` 维持的比例（任意尺寸都吃，但比例不对会被
  拉扁）；480 宽是 Inno 建议的「高 DPI 下不发虚」下限（202×386）
  的两倍多。
- ``assets/wizard-logo.png``     120×120 头部 logo，四角真透明。
  正方形是 ``WizardSmallImageFile`` 的要求（内置默认图 147×147）。

转换方式：

- 侧栏：源稿是纯竖向渐变（左右缘同色，已实测），抽出中列的颜色
  重采样到新高度——渐变与宽度无关，这样得到的正是原渐变的拉伸，
  而不是拿五个采样点重新拟合（R 通道在顶部有饱和，线性拟合会偏）。
- logo：源稿用 (255,0,255) 品红做色键，逐像素换成 alpha 通道；
  原图是硬边缘（无抗锯齿混合色），转换是像素级精确的。

改设计：编辑源稿 .bmp 后重跑本脚本；改产物 .png 会被下次覆盖。
跑法：``python tools/make_wizard_images.py``
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

#: 侧栏图目标尺寸：164:314 的比例取两倍（480×918），高 DPI 不糊。
SIDEBAR_SIZE = (480, 918)
#: logo 目标尺寸：正方形，120 是 Inno 内置默认图（147）的一个尺度。
LOGO_SIZE = (120, 120)
#: 源稿 logo 的色键透明色。Inno 6.4 起不再支持，转成 alpha 通道。
COLOR_KEY = (255, 0, 255)


def build_sidebar(source: Path, target: Path) -> None:
    """把源稿的竖向渐变重采样到目标尺寸。"""
    image = Image.open(source).convert("RGB")
    width, height = image.size
    # 只取中列：源稿每行左右同色（纯竖向渐变），重采样颜色列即整体。
    column = image.crop((width // 2, 0, width // 2 + 1, height))
    target_width, target_height = SIDEBAR_SIZE
    gradient = column.resize((1, target_height), Image.BILINEAR)
    gradient.resize((target_width, target_height), Image.BILINEAR).save(
        target, optimize=True
    )


def build_logo(source: Path, target: Path) -> None:
    """源稿 BMP 转 PNG：品键像素置为全透明，其余像素原样保留。"""
    image = Image.open(source).convert("RGB")
    image.putalpha(255)
    pixels = image.load()
    assert pixels is not None
    for x in range(image.width):
        for y in range(image.height):
            if pixels[x, y][:3] == COLOR_KEY:
                pixels[x, y] = (*COLOR_KEY, 0)
    image.save(target, optimize=True)


def main() -> None:
    sidebar = ASSETS / "wizard-sidebar.png"
    logo = ASSETS / "wizard-logo.png"
    build_sidebar(ASSETS / "wizard-sidebar.bmp", sidebar)
    build_logo(ASSETS / "wizard-logo.bmp", logo)
    print(f"已生成 {sidebar.relative_to(ROOT)}（{SIDEBAR_SIZE[0]}×{SIDEBAR_SIZE[1]}）")
    print(f"已生成 {logo.relative_to(ROOT)}（{LOGO_SIZE[0]}×{LOGO_SIZE[1]}，四角真透明）")


if __name__ == "__main__":
    main()
