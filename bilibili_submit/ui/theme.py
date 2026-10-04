"""界面主题：颜色、字体、间距的唯一来源。

组件里**不允许**出现颜色或尺寸字面量——改一个主色不该翻遍所有文件。
需要新颜色就在这里加，并给出用途注释。

设计规范：Windows 11 Fluent Design（退化到 Windows 10 亦可）

- 字体：Segoe UI（Windows 10/11 的系统 UI 字体）
- 配色：Windows 系统色板，浅色/深色两套
- 栅格：8px 为基准，4px 为半格
- 圆角：控件 6px（Fluent 要求 4~8px）
- 控件高度：按钮/输入框 32px，主要操作按钮 40px
- 状态：每个控件都有 default / hover / pressed / disabled / focus 五态

.. important::
   主题在**启动时**确定，运行期不热切换。tk/ttk 的控件颜色是在创建时
   写进控件的，改色板不会影响已经建好的控件——要真正切换得重建整个
   界面。与其做一个「切了不彻底」的开关，不如明确：设置里改完重启生效。

.. note::
   ttk 有两条硬边界，这里用降级方案而不是假装实现了：

   1. **输入框/下拉框无法圆角**：ttk 的 field 由引擎绘制，
      ``borderwidth`` 只能给直角边框。降级为 1px 细边框 + 聚焦环，
      视觉层级靠底色和边框区分。按钮是自绘的（:class:`FluentButton`），
      所以按钮有真圆角。
   2. **没有原生阴影**：tkinter 不提供 drop shadow。Fluent 的 elevation
      改用「卡片底色 + 1px 边框 + 更小的内圆角」三层来表达。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # 只为类型标注，运行期不需要 tkinter
    from tkinter import ttk

# ---------- 栅格与尺寸 ----------

#: 基础栅格。所有间距都应是它的整数倍（4 为半格，用于紧凑处）。
GRID = 8

PAD_XS = 4          # 半格：控件与文字之间
PAD_SM = 8          # 1 格：表单行间距
PAD_MD = 12         # 1.5 格：控件内边距
PAD_LG = 16         # 2 格：卡片内边距
PAD_XL = 24         # 3 格：分区之间
PAD_2XL = 32        # 4 格：页面级留白

#: 控件圆角。Fluent 的按钮/输入框是 4~8px，取中间值。
RADIUS_CONTROL = 6
#: 卡片圆角，比控件大一点，层级才分得开
RADIUS_CARD = 8

#: 标准控件高度（按钮、输入框、下拉框）
CONTROL_HEIGHT = 32
#: 主要操作按钮更高，用来引导视线
PRIMARY_BUTTON_HEIGHT = 40
#: 控件水平内边距
CONTROL_PAD_X = 12

NAV_WIDTH = 200               # 侧边导航栏宽度
NAV_ITEM_HEIGHT = 40
#: 导航项之间的间隙（半格）。项高本身是 40，间隙再按整格给就太散了。
NAV_ITEM_GAP = 4
#: 选中指示条：Fluent 的 NavigationView 用一条圆角竖条标出当前项
NAV_BAR_WIDTH = 3
NAV_BAR_HEIGHT = 24
INPUT_HEIGHT = CONTROL_HEIGHT
BUTTON_HEIGHT = CONTROL_HEIGHT
QR_SIZE = 220                 # 登录二维码边长
#: 列表行高。中文 + 行内状态文字，低于 32 会挤；高于 36 则一屏看不到几行。
ROW_HEIGHT = 36

MIN_WIDTH = 960
MIN_HEIGHT = 640
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 720

# ---------- 字体 ----------


def _pick_family() -> str:
    """挑一个该平台大概率存在的 UI 字体。

    Windows 上用 Segoe UI——Fluent Design 的标准字体，也是系统默认，
    不会像写死 "Microsoft YaHei" 那样在高 DPI 下显得厚重。
    Linux/macOS 回退到各自的 CJK 字体，否则中文渲染成方框。
    """
    if sys.platform == "win32":
        return "Segoe UI"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


FAMILY = _pick_family()

FONT_SMALL = (FAMILY, 9)
FONT_NORMAL = (FAMILY, 10)
FONT_MEDIUM = (FAMILY, 11)
FONT_LARGE = (FAMILY, 14)
FONT_TITLE = (FAMILY, 20)
FONT_MONO = ("Cascadia Mono", 9) if sys.platform == "win32" else ("DejaVu Sans Mono", 9)

# ---------- 色板 ----------

#: 浅色：Windows 11 系统色板（SolidBackgroundFillColorBase 等）
_LIGHT = {
    "BG": "#F3F3F3",                # 窗口底（Mica 下偏灰）
    "SURFACE": "#FFFFFF",           # 卡片/输入区
    "SURFACE_ALT": "#F9F9F9",       # 表头、次级区域
    "CARD_BORDER": "#EBEBEB",       # 卡片描边（替代阴影表达层级）
    "HOVER": "#F6F6F6",             # 悬停底色
    "PRESSED": "#EFEFEF",           # 按下底色
    "TEXT": "#1B1B1B",
    "TEXT_SECONDARY": "#616161",
    "TEXT_MUTED": "#9E9E9E",
    "TEXT_ON_PRIMARY": "#FFFFFF",
    "BORDER": "#E5E5E5",            # 常规分隔线
    "BORDER_STRONG": "#C7C7C7",     # 输入框/次按钮边框
    "PRIMARY": "#0067C0",           # Windows 11 强调色
    "PRIMARY_HOVER": "#005FB8",
    "PRIMARY_PRESSED": "#00539E",
    "PRIMARY_SOFT": "#E8F2FC",      # 主色浅底（选中行）
    "COLLAPSE_BG": "#F0F0F0",       # 折叠标题栏：中性，面积大不刺眼
    "FIELD_BG": "#FFFFFF",          # 输入框底
    "PRIMARY_RING": "#99C7F5",      # 聚焦环
    "DISABLED_BG": "#F3F3F3",
    "DISABLED_FG": "#9E9E9E",
    "NAV_BG": "#EAEAEA",        # 比窗口底略深一档，侧栏才分得出来
    "NAV_FG": "#1B1B1B",
    "NAV_HOVER": "#E0E0E0",
    "NAV_ACTIVE_BG": "#E8F2FC",
    "NAV_ACTIVE_FG": "#00539E",
    "LOG_BG": "#F5F5F5",            # 日志浅底：深色大块在浅色界面里太突兀
    "LOG_TEXT": "#1B1B1B",
}

#: 深色：Windows 11 深色色板。强调色换成亮蓝，
#: 因为深色底上的饱和蓝 (#0067C0) 会显得发闷且对比度不足。
_DARK = {
    "BG": "#202020",
    "SURFACE": "#2B2B2B",
    "SURFACE_ALT": "#333333",
    "CARD_BORDER": "#3B3B3B",
    "HOVER": "#333333",
    "PRESSED": "#3D3D3D",
    "TEXT": "#FFFFFF",
    "TEXT_SECONDARY": "#C7C7C7",
    "TEXT_MUTED": "#8A8A8A",
    # 深色模式的强调色是亮蓝，上面必须配深色文字才够对比度
    "TEXT_ON_PRIMARY": "#1B1B1B",
    "BORDER": "#3B3B3B",
    "BORDER_STRONG": "#545454",
    "PRIMARY": "#4CC2FF",
    "PRIMARY_HOVER": "#6ECCFF",
    "PRIMARY_PRESSED": "#8AD4FF",
    "PRIMARY_SOFT": "#0E3A52",
    "COLLAPSE_BG": "#333333",
    "FIELD_BG": "#383838",
    "PRIMARY_RING": "#2E7FB8",
    "DISABLED_BG": "#333333",
    "DISABLED_FG": "#6E6E6E",
    "NAV_BG": "#1A1A1A",       # 比窗口底再深一档，浅色版同理
    "NAV_FG": "#FFFFFF",
    "NAV_HOVER": "#2B2B2B",
    "NAV_ACTIVE_BG": "#0E3A52",
    "NAV_ACTIVE_FG": "#4CC2FF",
    "LOG_BG": "#141414",
    "LOG_TEXT": "#E6E6E6",
}

#: 语义色：**成对**给出前景与浅底。
#:
#: 状态标签、列表行都用「浅底 + 深字」而不是「深底 + 白字」——
#: 大面积深色块会让列表看起来像报错一片，浅底则既能一眼区分
#: 又不打断阅读。语义名同时也是 Treeview 的 tag 名，见
#: :func:`apply_tree_tags`。
_LIGHT_TONES = {
    "ok": ("#0E700E", "#E7F6E7"),        # 成功
    "warn": ("#9A6700", "#FDF6E3"),      # 警告（ffmpeg 缺失等）
    "error": ("#C42B1C", "#FDE7E9"),     # 失败
    "busy": ("#00539E", "#E8F2FC"),      # 进行中
    "missing": ("#9A6700", "#FDF6E3"),   # 文件缺失
    "info": ("#4A4A4A", "#F3F3F3"),      # 中性信息
    "idle": ("#616161", "#FFFFFF"),      # 待处理：不给底色，保持安静
}

_DARK_TONES = {
    "ok": ("#6CCB6C", "#14331A"),
    "warn": ("#F0C56B", "#3B2E14"),
    "error": ("#FF99A4", "#3D1A1F"),
    "busy": ("#4CC2FF", "#0E3A52"),
    "missing": ("#F0C56B", "#3B2E14"),
    "info": ("#C7C7C7", "#2B2B2B"),
    "idle": ("#8A8A8A", "#2B2B2B"),
}

#: 当前生效的色板名。``"light"`` / ``"dark"``
MODE = "light"

# 下列常量由 :func:`set_mode` 填充，这里先给浅色值，
# 好让「还没调用 set_mode 就想 import 主题」的场合也有合理默认。
BG = _LIGHT["BG"]
SURFACE = _LIGHT["SURFACE"]
SURFACE_ALT = _LIGHT["SURFACE_ALT"]
CARD_BORDER = _LIGHT["CARD_BORDER"]
HOVER = _LIGHT["HOVER"]
PRESSED = _LIGHT["PRESSED"]
TEXT = _LIGHT["TEXT"]
TEXT_SECONDARY = _LIGHT["TEXT_SECONDARY"]
TEXT_MUTED = _LIGHT["TEXT_MUTED"]
TEXT_ON_PRIMARY = _LIGHT["TEXT_ON_PRIMARY"]
BORDER = _LIGHT["BORDER"]
BORDER_STRONG = _LIGHT["BORDER_STRONG"]
PRIMARY = _LIGHT["PRIMARY"]
PRIMARY_DARK = _LIGHT["PRIMARY_PRESSED"]   # 旧名：按下/悬停用的深色
PRIMARY_HOVER = _LIGHT["PRIMARY_HOVER"]
PRIMARY_PRESSED = _LIGHT["PRIMARY_PRESSED"]
PRIMARY_SOFT = _LIGHT["PRIMARY_SOFT"]
COLLAPSE_BG = _LIGHT["COLLAPSE_BG"]
FIELD_BG = _LIGHT["FIELD_BG"]
PRIMARY_RING = _LIGHT["PRIMARY_RING"]
DISABLED_BG = _LIGHT["DISABLED_BG"]
DISABLED_FG = _LIGHT["DISABLED_FG"]
NAV_BG = _LIGHT["NAV_BG"]
NAV_FG = _LIGHT["NAV_FG"]
NAV_HOVER = _LIGHT["NAV_HOVER"]
NAV_ACTIVE_BG = _LIGHT["NAV_ACTIVE_BG"]
NAV_ACTIVE_FG = _LIGHT["NAV_ACTIVE_FG"]
LOG_BG = _LIGHT["LOG_BG"]
LOG_TEXT = _LIGHT["LOG_TEXT"]

TONES: dict[str, tuple[str, str]] = dict(_LIGHT_TONES)

# 旧名的兼容别名：新代码请用 :data:`TONES`（成对取色），
# 这三个只保留给还没迁移的调用点，值取自 TONES 以免两处漂移。
SUCCESS = TONES["ok"][0]
WARNING = TONES["warn"][0]
DANGER = TONES["error"][0]


def palette_for(mode: str) -> dict[str, str]:
    """取某个模式的完整色板。认不出的模式给浅色。"""
    return dict(_DARK if mode == "dark" else _LIGHT)


def set_mode(mode: str) -> str:
    """切换主题并**就地更新**模块级色值，返回实际生效的模式。

    就地更新（而不是让调用方到处传色板对象）是因为现有代码都写成
    ``theme.SURFACE`` 这种直接取常量的形式；让常量跟着模式走，
    几百处引用不用改一行。
    """
    global MODE, BG, SURFACE, SURFACE_ALT, CARD_BORDER, HOVER, PRESSED
    global TEXT, TEXT_SECONDARY, TEXT_MUTED, TEXT_ON_PRIMARY
    global BORDER, BORDER_STRONG
    global PRIMARY, PRIMARY_DARK, PRIMARY_HOVER, PRIMARY_PRESSED
    global PRIMARY_SOFT, PRIMARY_RING, DISABLED_BG, DISABLED_FG
    global COLLAPSE_BG, FIELD_BG
    global NAV_BG, NAV_FG, NAV_HOVER, NAV_ACTIVE_BG, NAV_ACTIVE_FG
    global LOG_BG, LOG_TEXT, TONES, SUCCESS, WARNING, DANGER

    mode = mode if mode in ("light", "dark") else "light"
    MODE = mode
    palette = palette_for(mode)

    BG = palette["BG"]
    SURFACE = palette["SURFACE"]
    SURFACE_ALT = palette["SURFACE_ALT"]
    CARD_BORDER = palette["CARD_BORDER"]
    HOVER = palette["HOVER"]
    PRESSED = palette["PRESSED"]
    TEXT = palette["TEXT"]
    TEXT_SECONDARY = palette["TEXT_SECONDARY"]
    TEXT_MUTED = palette["TEXT_MUTED"]
    TEXT_ON_PRIMARY = palette["TEXT_ON_PRIMARY"]
    BORDER = palette["BORDER"]
    BORDER_STRONG = palette["BORDER_STRONG"]
    PRIMARY = palette["PRIMARY"]
    PRIMARY_HOVER = palette["PRIMARY_HOVER"]
    PRIMARY_PRESSED = palette["PRIMARY_PRESSED"]
    PRIMARY_DARK = palette["PRIMARY_PRESSED"]
    PRIMARY_SOFT = palette["PRIMARY_SOFT"]
    COLLAPSE_BG = palette["COLLAPSE_BG"]
    FIELD_BG = palette["FIELD_BG"]
    PRIMARY_RING = palette["PRIMARY_RING"]
    DISABLED_BG = palette["DISABLED_BG"]
    DISABLED_FG = palette["DISABLED_FG"]
    NAV_BG = palette["NAV_BG"]
    NAV_FG = palette["NAV_FG"]
    NAV_HOVER = palette["NAV_HOVER"]
    NAV_ACTIVE_BG = palette["NAV_ACTIVE_BG"]
    NAV_ACTIVE_FG = palette["NAV_ACTIVE_FG"]
    LOG_BG = palette["LOG_BG"]
    LOG_TEXT = palette["LOG_TEXT"]

    TONES = dict(_DARK_TONES if mode == "dark" else _LIGHT_TONES)
    SUCCESS = TONES["ok"][0]
    WARNING = TONES["warn"][0]
    DANGER = TONES["error"][0]
    return mode


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


def apply(style: "ttk.Style", mode: str | None = None) -> str:
    """把主题应用到 ``ttk.Style``，返回生效的模式。

    ttk 原生组件在 Windows 上默认很难看，这里统一换掉配色与内边距，
    并把五态（默认/悬停/按下/禁用/聚焦）补齐。
    传进来的对象只要有 ``configure``/``map`` 即可，方便测试时传假对象。
    """
    if mode is not None:
        set_mode(mode)

    style.configure(".", background=BG, foreground=TEXT, font=FONT_NORMAL)

    # 卡片/容器。卡片用 1px 描边代替阴影表达层级（tkinter 没有阴影）
    style.configure("TFrame", background=BG)
    style.configure(
        "Card.TFrame", background=SURFACE, relief="solid", borderwidth=1,
        bordercolor=CARD_BORDER,
    )
    style.configure("Surface.TFrame", background=SURFACE)

    # 侧边导航
    style.configure("Nav.TFrame", background=NAV_BG)
    style.configure(
        "Nav.TButton",
        background=NAV_BG,
        foreground=NAV_FG,
        borderwidth=0,
        relief="flat",
        anchor="w",
        padding=(PAD_LG, PAD_SM + 2),
        font=FONT_NORMAL,
    )
    style.map(
        "Nav.TButton",
        background=[("selected", NAV_ACTIVE_BG), ("active", NAV_HOVER)],
        foreground=[("selected", NAV_ACTIVE_FG), ("active", NAV_FG)],
    )

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
    style.configure("Nav.TLabel", background=NAV_BG, foreground=NAV_FG)
    style.configure("Nav.Secondary.TLabel", background=NAV_BG, foreground=TEXT_MUTED)
    style.configure("Title.TLabel", background=SURFACE, foreground=TEXT, font=FONT_TITLE)
    style.configure("Heading.TLabel", background=SURFACE, foreground=TEXT, font=FONT_LARGE)

    # 按钮。圆角由自绘的 FluentButton 提供；这里是 ttk 版本的兜底样式，
    # 高度靠 padding 凑到 32 / 40，五态齐全。
    style.configure(
        "Primary.TButton",
        background=PRIMARY,
        foreground=TEXT_ON_PRIMARY,
        borderwidth=0,
        font=FONT_MEDIUM,
        padding=(PAD_XL, PAD_SM + 2),
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", DISABLED_BG),
            ("pressed", PRIMARY_PRESSED),
            ("active", PRIMARY_HOVER),
        ],
        foreground=[("disabled", DISABLED_FG)],
    )
    style.configure(
        "Secondary.TButton",
        background=SURFACE,
        foreground=TEXT,
        borderwidth=1,
        relief="solid",
        font=FONT_NORMAL,
        padding=(PAD_LG, PAD_XS + 2),
    )
    style.map(
        "Secondary.TButton",
        background=[
            ("disabled", DISABLED_BG),
            ("pressed", PRESSED),
            ("active", HOVER),
        ],
        foreground=[("disabled", DISABLED_FG), ("active", TEXT)],
        bordercolor=[
            ("disabled", CARD_BORDER),
            ("focus", PRIMARY),
            ("active", BORDER_STRONG),
        ],
    )

    # 输入框：白底细边，聚焦时主色环
    style.configure(
        "TEntry",
        fieldbackground=FIELD_BG,
        foreground=TEXT,
        bordercolor=BORDER_STRONG,
        lightcolor=BORDER_STRONG,
        padding=(CONTROL_PAD_X, PAD_XS + 2),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", PRIMARY), ("disabled", CARD_BORDER)],
        lightcolor=[("focus", PRIMARY_RING)],
        fieldbackground=[("disabled", DISABLED_BG)],
        foreground=[("disabled", DISABLED_FG)],
        insertbackground=[("disabled", DISABLED_BG)],
    )

    # 勾选框
    style.configure(
        "TCheckbutton",
        background=SURFACE,
        foreground=TEXT,
        font=FONT_NORMAL,
        padding=(PAD_XS, PAD_XS),
    )
    style.map(
        "TCheckbutton",
        background=[("active", SURFACE), ("disabled", SURFACE)],
        foreground=[
            ("disabled", DISABLED_FG),
            ("active", PRIMARY_HOVER),
            ("selected", PRIMARY),
        ],
    )

    # 下拉框
    style.configure(
        "TCombobox",
        fieldbackground=FIELD_BG,
        background=SURFACE,
        arrowcolor=TEXT,
        arrowsize=14,
        foreground=TEXT,
        bordercolor=BORDER_STRONG,
        lightcolor=BORDER_STRONG,
        padding=(CONTROL_PAD_X, PAD_XS + 2),
    )
    style.map(
        "TCombobox",
        bordercolor=[("focus", PRIMARY), ("disabled", CARD_BORDER)],
        lightcolor=[("focus", PRIMARY_RING)],
        fieldbackground=[("readonly", SURFACE), ("disabled", DISABLED_BG)],
        foreground=[("disabled", DISABLED_FG)],
        arrowcolor=[("disabled", DISABLED_FG)],
    )

    # 进度条
    style.configure(
        "TProgressbar", background=PRIMARY, troughcolor=BORDER, borderwidth=0,
        thickness=6,
    )

    # 列表（Treeview）
    style.configure(
        "Treeview",
        background=SURFACE,
        fieldbackground=SURFACE,
        foreground=TEXT,
        borderwidth=0,
        rowheight=ROW_HEIGHT,
    )
    style.configure(
        "Treeview.Heading",
        background=SURFACE_ALT,
        foreground=TEXT_SECONDARY,
        font=FONT_SMALL,
        borderwidth=0,
        relief="flat",
        padding=(PAD_SM, PAD_SM),
    )
    style.map(
        "Treeview",
        background=[("selected", PRIMARY_SOFT)],
        foreground=[("selected", TEXT)],
    )

    # 分隔线
    style.configure("TSeparator", background=BORDER)

    # 滚动条。Fluent 的滚动条是**无箭头**的细条——clam 默认在两端画
    # 三角按钮，深色模式下箭头区露出浅底、非常扎眼，所以干脆去掉，
    # 只留滑轨和滑块。本项目只用纵向滚动条，水平方向不存在。
    style.layout(
        "TScrollbar",
        [
            (
                "Scrollbar.trough",
                {
                    "children": [
                        ("Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
                    ],
                    "sticky": "ns",
                },
            )
        ],
    )
    style.configure(
        "TScrollbar", background=BORDER_STRONG, troughcolor=SURFACE_ALT,
        borderwidth=0, relief="flat", arrowsize=14,
    )
    style.map(
        "TScrollbar",
        background=[("disabled", CARD_BORDER), ("active", TEXT_MUTED)],
    )

    # 多页签（本项目的导航不用 notebook，但保留样式以防后续扩展）
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=SURFACE_ALT, padding=(PAD_MD, PAD_SM))
    style.map("TNotebook.Tab", background=[("selected", SURFACE)])
    return MODE
