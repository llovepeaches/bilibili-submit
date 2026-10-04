"""界面主题：颜色、字体、间距的唯一来源。

组件里**不允许**出现颜色或尺寸字面量——改一个主色不该翻遍所有文件。
需要新颜色就在这里加，并给出用途注释。

配色思路：B 站品牌粉作主色点缀，其余走中性灰。
粉色只用在「可点击/当前选中/进行中」这类需要引导注意的地方，
大面积铺粉会显得廉价且容易视觉疲劳。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只为类型标注，运行期不需要 tkinter
    from tkinter import ttk

# ---------- 颜色 ----------

PRIMARY = "#FB7299"           # B 站粉：主按钮、选中态、进行中
PRIMARY_DARK = "#E5658A"      # 主按钮按下/悬停
PRIMARY_SOFT = "#FFF0F5"      # 选中行底色、主色系浅底
PRIMARY_RING = "#FBB8CE"      # 输入框聚焦环（比主色浅，不至于抢眼）

BG = "#F4F5F7"                # 窗口底色
SURFACE = "#FFFFFF"           # 卡片、输入区
SURFACE_ALT = "#F7F8FA"       # 表头、交替行
HOVER = "#EFF1F4"             # 悬停底色

TEXT = "#1D2129"              # 主文字
TEXT_SECONDARY = "#61666D"    # 说明文字、标签
TEXT_MUTED = "#9499A0"        # 占位符、禁用态
TEXT_ON_PRIMARY = "#FFFFFF"   # 主按钮上的文字

BORDER = "#E5E7EB"            # 常规分隔线
BORDER_STRONG = "#D0D4DA"     # 输入框边框

LOG_BG = "#1F2023"            # 日志区深色底
LOG_TEXT = "#D6D8DA"

#: 语义色：**成对**给出前景与浅底。
#:
#: 状态标签、列表行都用「浅底 + 深字」而不是「深底 + 白字」——
#: 大面积深色块会让列表看起来像报错一片，浅底则既能一眼区分
#: 又不打断阅读。语义名同时也是 Treeview 的 tag 名，见
#: :func:`apply_tree_tags`。
TONES: "dict[str, tuple[str, str]]" = {
    "ok": ("#0E9F6E", "#E8F8F0"),       # 成功
    "warn": ("#C4700A", "#FFF5E6"),     # 警告（ffmpeg 缺失等）
    "error": ("#D93A34", "#FDECEC"),    # 失败
    "busy": ("#E5658A", "#FFF0F5"),     # 进行中
    "missing": ("#C4700A", "#FFF5E6"),  # 文件缺失
    "info": ("#4E5969", "#F2F3F5"),     # 中性信息
    "idle": ("#9499A0", "#FFFFFF"),     # 待处理：不给底色，保持安静
}

# 旧名的兼容别名：新代码请用 :data:`TONES`（成对取色），
# 这三个只保留给还没迁移的调用点，值取自 TONES 以免两处漂移。
SUCCESS = TONES["ok"][0]
WARNING = TONES["warn"][0]
DANGER = TONES["error"][0]


def tone(name: str) -> tuple[str, str]:
    """取语义色的 ``(前景, 浅底)``，未知名字退到 ``idle``。"""
    return TONES.get(name, TONES["idle"])


def apply_tree_tags(tree: "object") -> None:
    """给 Treeview 配好状态行着色，是**唯一**的配色来源。

    ttk 的 Treeview 行着色只能靠 tag，散在各视图里各配一份迟早会
    不一致（而且忘了配就是黑字一片）。这里集中配一次。

    用法——每行**只打一个**状态 tag::

        tree.item(iid, values=..., tags=(tone,))

    .. note::
       刻意**不做斑马纹**。多个 tag 同时作用于一行时哪个 ``background``
       生效取决于 Tk 内部实现，且 tag 背景与 ``selected`` 态的覆盖关系
       也不确定。每行只用一个 tag 就从根上避开了这个问题，
       而任务列表里「扫一眼看出成败」本来也比横向对齐更有价值。
    """
    for name, (fg, bg) in TONES.items():
        tree.tag_configure(name, foreground=fg, background=bg)

# ---------- 字体 ----------


def _pick_family() -> str:
    """挑一个该平台大概率存在的中文字体。

    写死 "Microsoft YaHei" 在 Linux 上会渲染成方框，所以按平台选，
    都取不到就用 Tk 默认字体（至少不会崩）。
    """
    if sys.platform == "win32":
        return "Microsoft YaHei UI"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


FAMILY = _pick_family()

FONT_SMALL = (FAMILY, 9)
FONT_NORMAL = (FAMILY, 10)
FONT_MEDIUM = (FAMILY, 11)
FONT_LARGE = (FAMILY, 13)
FONT_TITLE = (FAMILY, 16)
FONT_MONO = ("Consolas", 9) if sys.platform == "win32" else ("DejaVu Sans Mono", 9)

# ---------- 间距 ----------

PAD_XS = 4
PAD_SM = 8
PAD_MD = 12
PAD_LG = 16
PAD_XL = 24

# ---------- 尺寸 ----------

NAV_WIDTH = 180               # 侧边导航栏宽度
NAV_ITEM_HEIGHT = 42
INPUT_HEIGHT = 30
BUTTON_HEIGHT = 32
QR_SIZE = 220                 # 登录二维码边长

MIN_WIDTH = 880
MIN_HEIGHT = 600
DEFAULT_WIDTH = 1020
DEFAULT_HEIGHT = 700


def apply(style: "ttk.Style") -> None:
    """把主题应用到 ``ttk.Style``。

    ttk 原生组件在 Windows 上默认很难看，这里统一换掉配色与内边距。
    传进来的对象只要有 ``configure``/``map`` 即可，方便测试时传假对象。
    """
    style.configure(".", background=BG, foreground=TEXT, font=FONT_NORMAL)

    # 卡片/容器
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE, relief="flat")
    style.configure("Surface.TFrame", background=SURFACE)

    # 文字
    style.configure("TLabel", background=BG, foreground=TEXT, font=FONT_NORMAL)
    style.configure("Card.TLabel", background=SURFACE, foreground=TEXT)
    style.configure(
        "Secondary.TLabel", background=BG, foreground=TEXT_SECONDARY, font=FONT_SMALL
    )
    style.configure(
        "Card.Secondary.TLabel", background=SURFACE, foreground=TEXT_SECONDARY,
        font=FONT_SMALL,
    )
    style.configure("Title.TLabel", background=SURFACE, foreground=TEXT, font=FONT_TITLE)
    style.configure("Heading.TLabel", background=SURFACE, foreground=TEXT, font=FONT_LARGE)

    # 按钮：主按钮用品牌粉，次按钮描边
    style.configure(
        "Primary.TButton",
        background=PRIMARY,
        foreground=TEXT_ON_PRIMARY,
        borderwidth=0,
        font=FONT_MEDIUM,
        padding=(PAD_LG, PAD_SM),
    )
    style.map(
        "Primary.TButton",
        background=[("active", PRIMARY_DARK), ("disabled", BORDER_STRONG)],
        foreground=[("disabled", TEXT_MUTED)],
    )
    style.configure(
        "Secondary.TButton",
        background=SURFACE,
        foreground=TEXT,
        borderwidth=1,
        relief="solid",
        font=FONT_NORMAL,
        padding=(PAD_MD, PAD_SM - 2),
    )
    style.map(
        "Secondary.TButton",
        background=[("active", PRIMARY_SOFT)],
        bordercolor=[("active", PRIMARY)],
    )

    # 输入框：聚焦时给一圈浅粉，比默认的细蓝框更容易注意到
    style.configure(
        "TEntry",
        fieldbackground=SURFACE,
        foreground=TEXT,
        bordercolor=BORDER_STRONG,
        lightcolor=BORDER_STRONG,
        padding=(PAD_SM + 2, PAD_XS + 3),
    )
    style.map("TEntry", bordercolor=[("focus", PRIMARY)],
              lightcolor=[("focus", PRIMARY_RING)])

    # 下拉框
    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=TEXT,
        padding=(PAD_SM, PAD_XS + 2),
    )

    # 进度条
    style.configure(
        "TProgressbar", background=PRIMARY, troughcolor=BORDER, borderwidth=0,
        thickness=6,
    )

    # 列表（Treeview）：行高放宽到 32，表头去掉立体边框更像现代表格
    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        borderwidth=0,
        rowheight=32,
    )
    style.configure(
        "Treeview.Heading",
        background=SURFACE_ALT,
        foreground=TEXT_SECONDARY,
        font=FONT_SMALL,
        borderwidth=0,
        relief="flat",
    )
    style.map("Treeview", background=[("selected", PRIMARY_SOFT)],
              foreground=[("selected", TEXT)])

    # 分隔线
    style.configure("TSeparator", background=BORDER)

    # 多页签（本项目的导航不用 notebook，但保留样式以防后续扩展）
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=SURFACE_ALT, padding=(PAD_MD, PAD_SM))
    style.map("TNotebook.Tab", background=[("selected", SURFACE)])
