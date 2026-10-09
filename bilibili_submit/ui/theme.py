"""界面主题：颜色、字体、间距的唯一来源。

组件里**不允许**出现颜色或尺寸字面量——改一个主色不该翻遍所有文件。
需要新颜色就在这里加，并给出用途注释。

设计方向：Material 3 视觉 + B 站粉强调（详见项目根的 ``.impeccable.md``）
--------------------------------------------------------------------------

三件事决定了这套色板长这样：

1. **中性色带粉相。** 纯灰（``#F5F5F5``）在潜意识里是「Windows 灰」，
   和 B 站品牌没关系。所有中性色的 OKLCH 彩度都压到0.003~0.025、色相
   锚在 350——用户说不出为什么，但会觉得「这界面是哔哩的」。

2. **强调色是稀缺资源。** 粉色只出现在两个地方：主操作按钮、当前选中项。
   其余地方一律用墨色阶。粉色一旦到处出现就失效了。

3. **对比度用数字定，不用眼睛定。** 每个前景/底色组合都跑过 WCAG 公式，
   正文 ≥ 4.5:1、大字/UI 组件/焦点环 ≥ 3:1。几个关键值：

   ==========================  ========  ==========================================
   组合                对比度  结论
   ==========================  ========  ==========================================
   墨字 / 纸白（正文）      16.29:1  正文远超 AA
   弱化字 / 纸白            6.20:1  AA（数字化的中性灰是头号杀手，不能用）
   墨字 / 强调粉（主按钮）     6.41:1  AA —— **粉底必须配墨字，白字只有 2.64:1**
   深梅 / 纸白（焦点环）      6.86:1  焦点环用深梅，不是粉
   深色弱化字 / 深纸          5.07:1  AA
   ==========================  ========  ==========================================

形状语言：**能画出来的交互件都是药丸**。Material 3 的按钮和导航高亮
是全圆角（半径 = 高度一半），Canvas 自绘的按钮与导航项走
:data:`RADIUS_PILL`；ttk 引擎绘制的输入框/下拉框圆不了角，降级为
1px 细边框 + 聚焦环（见下方 note）——降级写在这里，不做假的圆角。

命名字体
--------

Tk 只有 ``normal`` / ``bold`` 两档字重，所以层级靠
**字号 + 字重 + 颜色**三个维度叠加，而不是指望字重更多。这里把用到的
组合注册成命名字体（``Bili.title`` 之类），组件只写 ``font="title"``：

- 不需要 Tk 根窗口存在，模块导入期就能拿到字体名（ttk 只认名字）；
- 想改字阶只改 :data:`FONT_ROLES` 一张表，不会出现三处字号不一样；
- 未知角色回落 body 而不是抛异常——字体不是业务逻辑，不该让界面崩。

.. important::
   主题在**启动时**确定，运行期不热切换。tk/ttk 的控件颜色是在创建时
   写进控件的，改色板不会影响已经建好的控件——要真正切换得重建整个
   界面。与其做一个「切了不彻底」的开关，不如明确：设置里改完重启生效。

.. note::
   ttk 有两条硬边界，这里用降级方案而不是假装实现了：

   1. **输入框/下拉框无法圆角**：ttk 的 field 由引擎绘制，
      ``borderwidth`` 只能给直角边框。降级为 1px 细边框 + 聚焦环，
      视觉层级靠底色和边框区分。按钮和导航高亮是 Canvas 自绘的，
      所以它们有真的药丸形。
   2. **没有原生阴影**：tkinter 不提供 drop shadow。Material 的
      elevation 改用「卡片底色 + 1px 边框」来表达——不画假的阴影。
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

#: 控件圆角。ttk 引擎绘制的控件（输入框、下拉框）画不出圆角，这个值
#: 只剩「降级说明」里的参考意义；Canvas 自绘的控件一律走 :data:`RADIUS_PILL`。
RADIUS_CONTROL = 6
#: 药丸圆角：Material 3 的按钮与导航高亮是**全圆角**（半径 = 高度一半）。
#: 给 999 表示「能有多圆就多圆」——绘制时会被 clamp 到边长的一半，
#: 32/40px 高的控件自然变成胶囊。
RADIUS_PILL = 999
#: 卡片圆角，比控件大一点，层级才分得开
RADIUS_CARD = 8

#: 标准控件高度（按钮、输入框、下拉框）
CONTROL_HEIGHT = 32
#: 主要操作按钮更高，用来引导视线
PRIMARY_BUTTON_HEIGHT = 40
#: 控件水平内边距
CONTROL_PAD_X = 12

#: 聚焦环粗细与环到控件的间隙。两者都 ≥2px，否则在浅底上根本看不见。
FOCUS_RING = 2
FOCUS_RING_GAP = 2

#: 侧栏宽度，两套：展开 200px（导航项带文字），折叠成图标栏 56px。
#:
#: 56 = :data:`GRID` * 7。折叠后导航项只剩一个汉字图标（约 28px），
#: 左右各留 12px 让它居中——再窄就挤了，再宽则图标栏看着像没排满。
#: 由 :mod:`.layout` 按窗口档位在两者之间切换。
NAV_WIDTH_EXPANDED = 200
NAV_WIDTH_COLLAPSED = 56
#: 默认（也是历史引用里）的侧栏宽度。测试和老代码都读这个名字，
#: 所以折叠值另起一个、不改这一个——``MIN_WIDTH > NAV_WIDTH + QR_SIZE``
#: 这条断言盯的就是展开态装得下二维码。
NAV_WIDTH = NAV_WIDTH_EXPANDED
NAV_ITEM_HEIGHT = 40
#: 导航项之间的间隙（半格）。项高本身是 40，间隙再按整格给就太散了。
NAV_ITEM_GAP = 4
#: Material 的导航高亮是**整行药丸**（不是旧版的左侧竖条）：
#: 药丸不满铺，两侧各留这条缝，形状才读得出来。
NAV_ITEM_MARGIN = 6
INPUT_HEIGHT = CONTROL_HEIGHT
BUTTON_HEIGHT = CONTROL_HEIGHT
QR_SIZE = 220                 # 登录二维码边长
#: 列表行高。中文 + 行内状态文字，低于 32 会挤；高于 36 则一屏看不到几行。
ROW_HEIGHT = 36
#: 日志区高度（文本行数）。给足上下文又不至于把上方表单挤没。
LOG_HEIGHT = 7

#: 最小窗口宽度。880 是让 :data:`~.layout.BREAKPOINT_MEDIUM`（900）
#: 真的能触发的下界——再小就没有「宽屏」可言，再大则最低那档断点
#: 永远跑不到，等于写了一段死代码。
#:
#: 侧栏在 880 下会折叠成 56px，内容区反而有 800px，比 1000 宽时的
#: 776px 还宽——这正是折叠的意义。
MIN_WIDTH = 880
MIN_HEIGHT = 660
DEFAULT_WIDTH = 1180
DEFAULT_HEIGHT = 760

# ---------- 字体 ----------

#: 命名字体的前缀。带上产品名，避免和用户自己注册的字体重名。
FONT_PREFIX = "Bili"


def _pick_family() -> str:
    """挑一个该平台大概率存在的 UI 字体。

    Windows 上用 Segoe UI——它是 Windows 那套设计体系（Fluent Design）
    的标准字体，也是系统默认，不会像写死 "Microsoft YaHei" 那样在高 DPI
    下显得厚重。Linux/macOS 回退到各自的 CJK 字体，否则中文渲染成方框。
    """
    if sys.platform == "win32":
        return "Segoe UI"
    if sys.platform == "darwin":
        return "PingFang SC"
    return "Noto Sans CJK SC"


def _pick_mono_family() -> str:
    """等宽字体只干一件事：让编号/路径/日志的字符对齐成列。"""
    return "Cascadia Mono" if sys.platform == "win32" else "DejaVu Sans Mono"


FAMILY = _pick_family()
MONO_FAMILY = _pick_mono_family()

#: 字阶表：``角色 -> (字号, 是否加粗, 是否等宽)``。
#:
#: 为什么只有两档字重：Tk 的``bold`` 就是全部了，想要第三档只能靠字号和
#: 颜色硬凑。所以层级靠**字号 + 字重 + 颜色**三者叠加——
#: 例如 caption(9/regular/muted) 与 body-strong(10/bold/ink) 之间的差别
#: 来自三个维度，不只是字号。
#:
#: 等宽只给``mono`` / ``mono-strong``：等宽字一旦用在普通界面上，
#: 整屏会立刻变成「终端」，是典型的 AI 味。
FONT_ROLES: dict[str, tuple[int, bool, bool]] = {
    "display": (17, True, False),        # 页面标题
    "title": (13, True, False),          # 卡片标题
    "subtitle": (11, False, False),      # 分区小标题
    "body": (10, False, False),          # 正文
    "body-strong": (10, True, False),    # 强调正文、按钮文字
    "caption": (9, False, False),        # 弱化说明
    "caption-strong": (9, True, False),  # 表头
    "mono": (9, False, True),            # 路径、编号
    "mono-strong": (10, True, True),     # 强调的编号/计数
}

#: 角色 -> 字体名。:func:`register_fonts` 填实，模块导入期先用名字占位。
_REGISTERED: dict[str, str] = {}

#: 已创建的 Font 对象。**必须持有引用**——``tkinter.font.Font`` 没有
#: 独立于 Python对象存活的概念，Font 实例被 GC 掉时Tk 端的字体定义
#: 也会随之消失，之后再按名字引用就是 ``TclError``。
_FONT_OBJECTS: list[object] = []


def font(role: str) -> str:
    """取字阶角色对应的**字体名**（ttk 的 ``font=`` 只认名字或元组）。

    未注册过 Tk 字体时也返回一个可读的名字——ttk 找不到会退回默认字体，
    这在测试里（没有根窗口）比抛异常好。
    """
    return _REGISTERED.get(role, f"{FONT_PREFIX}.{role}")


def register_fonts(force: bool = False) -> dict[str, str]:
    """把 :data:`FONT_ROLES` 注册成 Tk 命名字体，返回 ``角色 -> 字体名``。

    **需要活的 Tk 根窗口**，所以由 :func:`apply` 在建界面前调一次。

    用 ``tkfont.names()`` 查重保证幂等：测试会反复建/销毁根窗口，
    重复 ``tkfont.Font(name=...)`` 会抛 ``TclError``。

    拿已有字体要用 ``Font(name=..., exists=True)`` 或
    ``Font(font="Bili.body")``——直接 ``Font(name=...)`` 会被当成
    「新建一个叫这名字的字体」而失败。
    """
    from tkinter import font as tkfont

    existing = set(tkfont.names())
    # 上一轮根窗口被销毁时，Tk 端的字体定义已经随之消失，只剩 Python
    # 侧的僵尸对象。不清掉的话列表会随测试轮次一直涨。
    _FONT_OBJECTS[:] = [obj for obj in _FONT_OBJECTS if obj.name in existing]
    for role, (size, bold, mono) in FONT_ROLES.items():
        name = f"{FONT_PREFIX}.{role}"
        _REGISTERED[role] = name
        if not force and name in existing:
            continue
        obj = tkfont.Font(
            name=name,
            family=MONO_FAMILY if mono else FAMILY,
            size=size,
            weight="bold" if bold else "normal",
        )
        if obj.name != name:
            # 名字撞了 Tk 内部字体，Tk 会自动改名；记下实际名字
            _REGISTERED[role] = obj.name
        _FONT_OBJECTS.append(obj)
    return dict(_REGISTERED)


def measure_font(role: str = "body") -> object:
    """取一个用于测量文字宽度的 ``tkfont.Font``（走已注册的字阶）。

    组件自绘文字（Canvas 按钮）时用它算宽度——测量和绘制必须用
    同一个字体，否则中文按钮宽度会算错。
    """
    from tkinter import font as tkfont

    register_fonts()
    return tkfont.Font(font=font(role))


# 旧名 tuple：留着是因为 Canvas 自绘文字要传元组，且外部可能仍在引用。
# 数值一律从 FONT_ROLES 派生，不单独写死，免得两处漂移。
def _role_tuple(role: str) -> tuple[str, int]:
    size, bold, mono = FONT_ROLES[role]
    return (MONO_FAMILY if mono else FAMILY, size, "bold" if bold else "normal")


FONT_SMALL = _role_tuple("caption")
FONT_NORMAL = _role_tuple("body")
FONT_MEDIUM = _role_tuple("subtitle")
FONT_LARGE = _role_tuple("title")
FONT_TITLE = _role_tuple("display")
FONT_MONO = _role_tuple("mono")

# 新名：直接给字体名字符串，ttk 样式与 tk.Label 都能用
FONT_DISPLAY = font("display")
FONT_TITLE_ = font("title")
FONT_SUBTITLE = font("subtitle")
FONT_BODY = font("body")
FONT_BODY_STRONG = font("body-strong")
FONT_CAPTION = font("caption")
FONT_CAPTION_STRONG = font("caption-strong")
FONT_MONO_NAME = font("mono")
FONT_MONO_STRONG = font("mono-strong")

# ---------- 色板 ----------

#: 浅色。命名按**用途**而不是按颜色本身——``PAPER`` 比 ``#FDFAFB`` 更耐改：
#: 换主色时不用重读所有引用。全部值跑过 WCAG 审计，见模块文档。
_LIGHT = {
    # 表面三级：窗口 → 卡片 → 次级区域（表头、日志）
    "SHELL": "#F5F0F2",          # 窗口底，比卡片深一档
    "PAPER": "#FDFAFB",          # 卡片/ 输入区
    "PAPER_ALT": "#F7F1F3",      # 表头、日志底
    # 描边两级：常规分隔线 / 需要强调边界的控件
    "LINE": "#EADFE4",
    "LINE_STRONG": "#D8C7CF",
    # 墨色三级：正文 / 次要 / 弱化
    "INK": "#241A1F",            # 深梅墨，不用纯黑——纯黑在粉底旁边发死
    "INK_SECOND": "#4A3B42",
    "INK_MUTED": "#6B5A62",      # 6.20:1。**别再退回 #9E9E9E**，那个只有 2.68:1
    # 强调色
    "PINK": "#FB7299",
    "PINK_HOVER": "#F5638C",
    "PINK_PRESSED": "#DE5181",
    "PINK_DEEP": "#A62052",      # 6.86:1 —— 焦点环、可读文字上的粉色
    "PINK_TINT": "#FFF1F4",      # 选中行/ 提示条浅底
    "INK_ON_PINK": "#241A1F",    # 粉底上的文字必须是墨色（白字2.64:1 不达标）
    # 墨色实心块（底部操作条、状态栏）：文字反色
    "INK_SURFACE": "#241A1F",
    "INK_SURFACE_FG": "#FDFAFB",
    "INK_SURFACE_MUTED": "#C9AFB7",
    # 交互态
    "HOVER": "#F1E9EC",
    "PRESSED": "#E7DCE0",
    "FOCUS_RING": "#A62052",     # 焦点环 6.86:1（粉只有 2.64:1，绝不能用）
    "DISABLED_BG": "#EFE7EA",
    # 3.01:1 on DISABLED_BG。禁用文字在 WCAG 里可以豁免，但**这个应用不行**：
    # 「未登录时的主按钮」是需要读懂原因的，读不清等于没提示。
    "DISABLED_FG": "#93818A",
    # 导航
    # 侧栏底色**不能和 DISABLED_BG 同值**：两者一旦撞色，禁用按钮就长得
    # 和侧栏一个样，「这个按钮为什么点不动」在深色下尤其读不出来。
    # 这里比窗口底再深一档，让侧栏明确地「退到后面去」。
    "NAV_BG": "#E9E0E4",
    "NAV_FG": "#241A1F",
    "NAV_HOVER": "#E4D8DD",
    "NAV_ACTIVE_BG": "#FDFAFB",      # 选中项反白，像一张被抽出的单据
    "NAV_ACTIVE_FG": "#A62052",      # 选中文字用深梅，粉色当背景看不清
    # 日志
    "LOG_BG": "#F3EDF0",
    "LOG_TEXT": "#241A1F",
    # 表单
    "COLLAPSE_BG": "#F1E9EC",        # 折叠标题栏：中性、面积大不刺眼
    # 折叠标题栏的悬停态。**不能复用 HOVER**：两者色值相同的话，鼠标
    # 划过标题栏时底色纹丝不动，「这一行能点」就只剩光标在说——而光标
    # 快速划过最容易错过。这里必须比 COLLAPSE_BG 深一档才看得见。
    "COLLAPSE_HOVER": "#E4D6DC",
    "FIELD_BG": "#FFFFFF",           # 输入框比卡片更亮，边界才清楚
}

#: 深色。**不是浅色的反转**：深色用「更亮的表面」表达深度，靠明度而不是
#: 阴影分层；文字整体降字重，避免深底上的粗体糊成一块。绝不用纯黑#000。
#:
#: .. important::
#:    每一级表面必须有**自己的**明度。上一版有六个键共用三个色值
#:    （``PAPER_ALT``/``DISABLED_BG``/``COLLAPSE_BG`` 全是 ``#2A2126``，
#:    ``INK_SURFACE``/``FIELD_BG`` 都是 ``#2E2429``，``NAV_BG``/``LOG_BG``
#:    都是 ``#171215``），结果是深色模式下层级完全糊成一片：禁用按钮
#:    和表头一个色、操作条和输入框分不出来、侧栏和日志区连成一块。
#:    现在按明度排成十级阶梯，并有测试盯着「不许再有重复」——
#:    ``tests/test_theme_contrast.py::test_surfaces_form_a_distinct_ladder``。
#: 算出来的不是凑的：SHELL 固定在 ``#120E11``，而 ``PAPER`` 被弱化文字
#: 的 4.5:1 卡住上限，中间要塞进九级，等比排下来每级就是 1.04。
_DARK = {
    # ---- 表面十级阶梯：由深到浅 ----
    # 方向遵循一条规律：**「内含面」和「悬停态」永远朝中间灰走**。浅色下
    # 内含面比卡片暗（纸白 → 灰），深色下内含面比卡片亮（墨黑 → 灰）。
    # 所以两套色板的排序是镜像的，不能共用一份顺序表。
    "SHELL": "#120E11",          # ① 窗口底
    "NAV_BG": "#171316",         # ② 侧栏
    "LOG_BG": "#1C171B",         # ③ 日志区
    "PAPER": "#201B1F",          # ④ 卡片：所有东西的基准面
    "COLLAPSE_BG": "#241E22",    # ⑤ 折叠标题栏
    "DISABLED_BG": "#282126",    # ⑥ 禁用控件（比卡片亮，但最"钝"）
    "PAPER_ALT": "#2B2429",      # ⑦ 表头、次级面
    "COLLAPSE_HOVER": "#2E272C",  # ⑧ 折叠标题栏悬停：比 ⑤ 亮两级
    "FIELD_BG": "#312A2F",       # ⑨ 输入框
    "INK_SURFACE": "#352C33",    # ⑩ 操作条：深色下它是最亮的一档
    # ---- 描边两级：要比最亮的表面还亮，否则在深色上看不见边界 ----
    "LINE": "#382C33",
    "LINE_STRONG": "#4C3C45",
    # ---- 墨色三级 ----
    "INK": "#F6EEF1",
    "INK_SECOND": "#C9B8C0",
    # 5.57:1 on PAPER。比上一版（#9A8791）提亮了一档：卡片在九级阶梯里
    # 上移之后，弱化文字贴到悬停态的折叠标题栏上只剩 4.33:1，读不清。
    "INK_MUTED": "#A08F9A",
    # ---- 强调色 ----
    "PINK": "#FF9BB6",           # 深底上要提亮才够彩度
    "PINK_HOVER": "#FFB3C8",
    "PINK_PRESSED": "#E87C9C",
    "PINK_DEEP": "#FF9BB6",
    "PINK_TINT": "#3A1F2A",      # 深色下的「浅底」是深色
    "INK_ON_PINK": "#241A1F",
    # ---- 墨色实心块上的文字 ----
    "INK_SURFACE_FG": "#F6EEF1",
    "INK_SURFACE_MUTED": "#B39FA8",
    # ---- 交互态：hover/pressed 同样朝中间灰走，所以是**更亮** ----
    "HOVER": "#332830",
    "PRESSED": "#3E3138",
    "FOCUS_RING": "#FF9BB6",     # 焦点环 9.68:1 on SHELL
    "DISABLED_FG": "#7A6871",    # 3.03:1 on DISABLED_BG，理由同浅色
    # ---- 导航 ----
    "NAV_FG": "#F6EEF1",
    "NAV_HOVER": "#2A2126",
    "NAV_ACTIVE_BG": "#342930",  # 选中项反白，像一张被抽出的单据
    "NAV_ACTIVE_FG": "#FF9BB6",
    # ---- 日志 ----
    "LOG_TEXT": "#D9C9D0",
}

#: 语义色：**成对**给出前景与浅底。
#:
#: 状态标签、列表行都用「浅底 + 深字」而不是「深底 + 白字」——
#: 大面积深色块会让列表看起来像报错一片，浅底则既能一眼区分
#: 又不打断阅读。语义名同时也是 Treeview 的 tag 名，见
#: :func:`apply_tree_tags`。
_LIGHT_TONES = {
    "ok": ("#1B6B3A", "#E4F4E8"),        # 成功
    "warn": ("#8A5A00", "#FBF0DA"),      # 警告（ffmpeg 缺失等）
    "error": ("#B3261E", "#FCE7E5"),     # 失败
    "busy": ("#A62052", "#FFF1F4"),      # 进行中 —— 粉是「正在动」的意思
    "missing": ("#8A5A00", "#FBF0DA"),   # 文件缺失
    "info": ("#4A3B42", "#F1E9EC"),      # 中性信息
    "idle": ("#6B5A62", "#FDFAFB"),      # 待处理：不给底色，保持安静
}

_DARK_TONES = {
    "ok": ("#7DD39B", "#16301F"),
    "warn": ("#F0C56B", "#3A2D14"),
    "error": ("#FF9B94", "#3D1A1A"),
    "busy": ("#FF9BB6", "#3A1F2A"),
    "missing": ("#F0C56B", "#3A2D14"),
    "info": ("#C9B8C0", "#2B2429"),     # 底 = PAPER_ALT
    "idle": ("#9A8791", "#201B1F"),     # 底 = PAPER，让待处理行融进卡片
}

#: 语义色名 -> 配对的 glyph。状态**不能只靠颜色**传达——色盲用户看不出
#: 「粉色 = 进行中」。每个状态给一个形状不同的记号，双通道编码。
TONE_GLYPHS: dict[str, str] = {
    "ok": "✓",        # 对勾
    "warn": "!",        # 感叹号
    "error": "✕",        # 叉
    "busy": "◐",        # 半圆：正在转
    "missing": "?",     # 问号
    "info": "·",        # 点：不需要你处理
    "idle": "○",        # 空心圆：排队中
}

#: 新语义名 -> 旧名。别名存在的意义是**不破坏外部引用**：
#: 几百处 ``theme.SURFACE`` 不用一次改完，同时新代码有更准确的名字可用。
_ALIASES: dict[str, str] = {
    "BG": "SHELL",
    "SURFACE": "PAPER",
    "SURFACE_ALT": "PAPER_ALT",
    "CARD_BORDER": "LINE",
    "BORDER": "LINE",
    "BORDER_STRONG": "LINE_STRONG",
    "TEXT": "INK",
    "TEXT_SECONDARY": "INK_SECOND",
    "TEXT_MUTED": "INK_MUTED",
    "TEXT_ON_PRIMARY": "INK_ON_PINK",
    "PRIMARY": "PINK",
    "PRIMARY_HOVER": "PINK_HOVER",
    "PRIMARY_PRESSED": "PINK_PRESSED",
    "PRIMARY_DARK": "PINK_DEEP",
    "PRIMARY_SOFT": "PINK_TINT",
    "PRIMARY_RING": "FOCUS_RING",
}

#: 墨色实心块（底部操作条、状态栏）上用的状态色，**只有前景**。
#:
#: 不能直接复用 :data:`TONES` —— 那些前景是为「浅底深字」选的，
#: 压到墨底上会糊成一片。要单独给一组。
#: 八个组合在两种墨底上都是7.4:1 以上，见 ``tests/test_theme_contrast.py``。
_INK_TONES: dict[str, str] = {
    "ok": "#7DD39B",
    "warn": "#F0C56B",
    "error": "#FF9B94",
    "info": "#C9B8C0",
    "busy": "#FF9BB6",
    "missing": "#F0C56B",
    "idle": "#B7A5AD",
}

#: 当前生效的色板名。``"light"`` / ``"dark"``
MODE = "light"

# ---- 色值常量 ----
#
# 这些名字由 :func:`set_mode` 通过 ``globals().update()`` 赋值，静态分析
# 看不见它们。所以这里**显式给一遍浅色初值**——不是重复，是让
# pyflakes / IDE 知道这些名字存在：
#
# - **不要改成字典查表**：几百处 ``theme.PAPER`` 引用会全部失效，
#   而直接取常量的写法是这个项目刻意的选择（读代码时一眼看到颜色）。
# - **不要改成只有注解的声明**：那样运行时能跑、pyflakes 却会报
#   undefined，产生「假红」——比假绿更耗时间。
# - 真正的初值来源是下面的 :func:`set_mode`（模块末尾调用），这里这
#   份只是让静态分析闭嘴的同值副本。
SHELL = _LIGHT["SHELL"]
PAPER = _LIGHT["PAPER"]
PAPER_ALT = _LIGHT["PAPER_ALT"]
LINE = _LIGHT["LINE"]
LINE_STRONG = _LIGHT["LINE_STRONG"]
INK = _LIGHT["INK"]
INK_SECOND = _LIGHT["INK_SECOND"]
INK_MUTED = _LIGHT["INK_MUTED"]
INK_ON_PINK = _LIGHT["INK_ON_PINK"]
INK_SURFACE = _LIGHT["INK_SURFACE"]
INK_SURFACE_FG = _LIGHT["INK_SURFACE_FG"]
INK_SURFACE_MUTED = _LIGHT["INK_SURFACE_MUTED"]
PINK = _LIGHT["PINK"]
PINK_HOVER = _LIGHT["PINK_HOVER"]
PINK_PRESSED = _LIGHT["PINK_PRESSED"]
PINK_DEEP = _LIGHT["PINK_DEEP"]
PINK_TINT = _LIGHT["PINK_TINT"]
HOVER = _LIGHT["HOVER"]
PRESSED = _LIGHT["PRESSED"]
FOCUS_RING = _LIGHT["FOCUS_RING"]
DISABLED_BG = _LIGHT["DISABLED_BG"]
DISABLED_FG = _LIGHT["DISABLED_FG"]
COLLAPSE_BG = _LIGHT["COLLAPSE_BG"]
COLLAPSE_HOVER = _LIGHT["COLLAPSE_HOVER"]
FIELD_BG = _LIGHT["FIELD_BG"]
LOG_BG = _LIGHT["LOG_BG"]
LOG_TEXT = _LIGHT["LOG_TEXT"]
NAV_BG = _LIGHT["NAV_BG"]
NAV_FG = _LIGHT["NAV_FG"]
NAV_HOVER = _LIGHT["NAV_HOVER"]
NAV_ACTIVE_BG = _LIGHT["NAV_ACTIVE_BG"]
NAV_ACTIVE_FG = _LIGHT["NAV_ACTIVE_FG"]
# 旧名别名，键见 :data:`_ALIASES`
BG = SHELL
SURFACE = PAPER
SURFACE_ALT = PAPER_ALT
CARD_BORDER = LINE
BORDER = LINE
BORDER_STRONG = LINE_STRONG
TEXT = INK
TEXT_SECONDARY = INK_SECOND
TEXT_MUTED = INK_MUTED
TEXT_ON_PRIMARY = INK_ON_PINK
PRIMARY = PINK
PRIMARY_HOVER = PINK_HOVER
PRIMARY_PRESSED = PINK_PRESSED
PRIMARY_DARK = PINK_DEEP
PRIMARY_SOFT = PINK_TINT
PRIMARY_RING = FOCUS_RING


def palette_for(mode: str) -> dict[str, str]:
    """取某个模式的完整色板，**同时含新旧两套键名**。

    认不出的模式给浅色。返回新键（``PAPER``/``INK``/``PINK``）与旧键
    （``SURFACE``/``TEXT``/``PRIMARY``）的并集，老调用方和新调用方
    都能用同一份数据。
    """
    raw = dict(_DARK if mode == "dark" else _LIGHT)
    for alias, target in _ALIASES.items():
        raw[alias] = raw[target]
    return raw


def _names() -> list[str]:
    """所有色值常量的名字，含别名——``set_mode`` 靠它就地更新全局。"""
    return list(_LIGHT) + list(_ALIASES)


def set_mode(mode: str) -> str:
    """切换主题并**就地更新**模块级色值，返回实际生效的模式。

    就地更新（而不是让调用方到处传色板对象）是因为现有代码都写成
    ``theme.SURFACE`` 这种直接取常量的形式；让常量跟着模式走，
    几百处引用不用改一行。

    .. note::
       下面那行 ``globals().update()`` 是故意的，但代价是**静态分析
       看不见这些名字**——:mod:`pyflakes` 会把 ``apply()`` 里的
       ``PAPER``报成undefined。所以上面必须用显式声明把它们「喂」
       给分析器：删掉那些声明，lint 会立刻红，而颜色其实一直是对的。
       这类 workaround 值得写清楚，否则下一个人会以为是真 bug。
    """
    global MODE, TONES, SUCCESS, WARNING, DANGER

    mode = mode if mode in ("light", "dark") else "light"
    MODE = mode
    palette = palette_for(mode)

    # 逐个写回模块全局。函数里不能写 ``global a, b, c = ...``，
    # 所以先把名字收集起来再统一设置。
    globals().update({name: palette[name] for name in _names()})

    TONES = dict(_DARK_TONES if mode == "dark" else _LIGHT_TONES)
    SUCCESS = TONES["ok"][0]
    WARNING = TONES["warn"][0]
    DANGER = TONES["error"][0]
    return mode


def tone(name: str) -> tuple[str, str]:
    """取语义色的 ``(前景, 浅底)``，未知名字退到 ``idle``。"""
    return TONES.get(name, TONES["idle"])


def tone_on_ink(name: str) -> str:
    """取语义色在**墨色实心块**上的前景色，未知名字退到 ``idle``。

    和 :func:`tone` 分开是因为底色完全不同：墨底要亮色，浅底要深色，
    用同一组值必然有一边看不清。状态栏和操作条都用这个。
    """
    return _INK_TONES.get(name, _INK_TONES["idle"])


def tone_glyph(name: str) -> str:
    """取状态的形状记号，未知名字退到 ``idle`` 的空心圆。"""
    return TONE_GLYPHS.get(name, TONE_GLYPHS["idle"])


def apply_tree_tags(tree: object) -> None:
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
    register_fonts()

    style.configure(".", background=SHELL, foreground=INK, font=font("body"))

    # 卡片/容器。卡片用 1px 描边代替阴影表达层级（tkinter 没有阴影）
    style.configure("TFrame", background=SHELL)
    style.configure(
        "Card.TFrame", background=PAPER, relief="solid", borderwidth=1,
        bordercolor=LINE,
    )
    style.configure("Surface.TFrame", background=PAPER)
    # 墨色实心块：底部操作条、状态栏。文字反色，视觉上把「现在该做什么」
    # 和「正在填什么」分成两层。
    style.configure("Ink.TFrame", background=INK_SURFACE)
    style.configure(
        "Ink.TLabel", background=INK_SURFACE, foreground=INK_SURFACE_FG,
        font=font("subtitle"),
    )
    style.configure(
        "Ink.Secondary.TLabel", background=INK_SURFACE,
        foreground=INK_SURFACE_MUTED, font=font("caption"),
    )

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
        font=font("body"),
    )
    style.map(
        "Nav.TButton",
        background=[("selected", NAV_ACTIVE_BG), ("active", NAV_HOVER)],
        foreground=[("selected", NAV_ACTIVE_FG), ("active", NAV_FG)],
    )

    # 文字。层级 = 字号 + 字重 + 颜色 三个维度叠加（见 FONT_ROLES 说明）
    style.configure("TLabel", background=SHELL, foreground=INK, font=font("body"))
    style.configure("Card.TLabel", background=PAPER, foreground=INK)
    style.configure(
        "Secondary.TLabel", background=SHELL, foreground=INK_SECOND,
        font=font("caption"),
    )
    style.configure(
        "Card.Secondary.TLabel", background=PAPER, foreground=INK_SECOND,
        font=font("caption"),
    )
    style.configure("Nav.TLabel", background=NAV_BG, foreground=NAV_FG)
    style.configure(
        "Nav.Secondary.TLabel", background=NAV_BG, foreground=INK_MUTED
    )
    style.configure(
        "Title.TLabel", background=PAPER, foreground=INK, font=font("display")
    )
    style.configure(
        "Heading.TLabel", background=PAPER, foreground=INK, font=font("title")
    )
    style.configure(
        "Subtitle.TLabel", background=PAPER, foreground=INK_SECOND,
        font=font("subtitle"),
    )
    # 表单标签：比正文弱一档，让控件本身成为视觉重心
    style.configure(
        "Field.TLabel", background=PAPER, foreground=INK_SECOND,
        font=font("caption-strong"),
    )
    # 强调数值（计数、路径），用等宽字体扛「对齐」这件事
    style.configure(
        "Value.TLabel", background=PAPER, foreground=INK, font=font("mono-strong")
    )
    style.configure("Mono.TLabel", background=PAPER, foreground=INK_SECOND,
                    font=font("mono"))
    # 折叠区标题栏
    style.configure("Collapse.TFrame", background=COLLAPSE_BG)
    style.configure(
        "Collapse.TLabel", background=COLLAPSE_BG, foreground=INK,
        font=font("body-strong"),
    )
    style.configure(
        "Collapse.Caption.TLabel", background=COLLAPSE_BG, foreground=INK_MUTED,
        font=font("caption"),
    )

    # 按钮。药丸形由自绘的 MaterialButton 提供；这里是ttk 版本的兜底样式，
    # 高度靠 padding 凑到 32 / 40，五态齐全。
    style.configure(
        "Primary.TButton",
        background=PINK,
        foreground=INK_ON_PINK,     # 粉底配墨字，不是白字
        borderwidth=0,
        font=font("body-strong"),
        padding=(PAD_XL, PAD_SM + 2),
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", DISABLED_BG),
            ("pressed", PINK_PRESSED),
            ("active", PINK_HOVER),
        ],
        foreground=[("disabled", DISABLED_FG)],
    )
    style.configure(
        "Secondary.TButton",
        background=PAPER,
        foreground=INK,
        borderwidth=1,
        relief="solid",
        font=font("body"),
        padding=(PAD_LG, PAD_XS + 2),
    )
    style.map(
        "Secondary.TButton",
        background=[
            ("disabled", DISABLED_BG),
            ("pressed", PRESSED),
            ("active", HOVER),
        ],
        foreground=[("disabled", DISABLED_FG), ("active", INK)],
        bordercolor=[
            ("disabled", LINE),
            ("focus", FOCUS_RING),
            ("active", LINE_STRONG),
        ],
    )

    # 输入框：比卡片更亮的底 + 细边，聚焦时深梅色环
    style.configure(
        "TEntry",
        fieldbackground=FIELD_BG,
        foreground=INK,
        insertcolor=INK,
        bordercolor=LINE_STRONG,
        lightcolor=LINE_STRONG,
        padding=(CONTROL_PAD_X, PAD_XS + 2),
    )
    style.map(
        "TEntry",
        bordercolor=[("focus", PINK_DEEP), ("disabled", LINE)],
        lightcolor=[("focus", FOCUS_RING)],
        fieldbackground=[("disabled", DISABLED_BG)],
        foreground=[("disabled", DISABLED_FG)],
        insertbackground=[("disabled", DISABLED_BG)],
    )

    # 勾选框
    style.configure(
        "TCheckbutton",
        background=PAPER,
        foreground=INK,
        font=font("body"),
        padding=(PAD_XS, PAD_XS),
    )
    style.map(
        "TCheckbutton",
        background=[("active", PAPER), ("disabled", PAPER)],
        foreground=[
            ("disabled", DISABLED_FG),
            ("active", PINK_DEEP),
            ("selected", PINK),
        ],
    )

    # 下拉框
    style.configure(
        "TCombobox",
        fieldbackground=FIELD_BG,
        background=PAPER,
        arrowcolor=INK_SECOND,   # 显式给色：clam 默认黑箭头深色下看不见
        arrowsize=14,
        foreground=INK,
        bordercolor=LINE_STRONG,
        lightcolor=LINE_STRONG,
        padding=(CONTROL_PAD_X, PAD_XS + 2),
    )
    style.map(
        "TCombobox",
        bordercolor=[("focus", PINK_DEEP), ("disabled", LINE)],
        lightcolor=[("focus", FOCUS_RING)],
        fieldbackground=[("readonly", PAPER), ("disabled", DISABLED_BG)],
        foreground=[("disabled", DISABLED_FG)],
        arrowcolor=[("disabled", DISABLED_FG)],
        selectbackground=[("readonly", PINK), ("disabled", DISABLED_BG)],
        selectforeground=[("readonly", INK_ON_PINK), ("disabled", DISABLED_FG)],
    )

    # 进度条
    style.configure(
        "TProgressbar", background=PINK, troughcolor=LINE, borderwidth=0,
        thickness=6,
    )

    # 列表（Treeview）
    style.configure(
        "Treeview",
        background=PAPER,
        fieldbackground=PAPER,
        foreground=INK,
        borderwidth=0,
        rowheight=ROW_HEIGHT,
    )
    style.configure(
        "Treeview.Heading",
        background=PAPER_ALT,
        foreground=INK_SECOND,
        font=font("caption-strong"),
        borderwidth=0,
        relief="flat",
        padding=(PAD_SM, PAD_SM),
    )
    style.map(
        "Treeview",
        background=[("selected", PINK_TINT)],
        foreground=[("selected", INK)],
    )

    # 分隔线
    style.configure("TSeparator", background=LINE)

    # 滚动条。Windows 那套设计体系（Fluent）里滚动条是**无箭头**的细条
    # ——clam 默认在两端画三角按钮，深色模式下箭头区露出浅底、非常扎眼，
    # 所以干脆去掉，只留滑轨和滑块。本项目只用纵向滚动条，水平方向不存在。
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
        "TScrollbar", background=LINE_STRONG, troughcolor=PAPER_ALT,
        borderwidth=0, relief="flat", arrowsize=14,
    )
    style.map(
        "TScrollbar",
        background=[("disabled", LINE), ("active", INK_MUTED)],
    )

    # 多页签（本项目的导航不用 notebook，但保留样式以防后续扩展）
    style.configure("TNotebook", background=SHELL, borderwidth=0)
    style.configure("TNotebook.Tab", background=PAPER_ALT, padding=(PAD_MD, PAD_SM))
    style.map("TNotebook.Tab", background=[("selected", PAPER)])
    return MODE


# 导入即给浅色值：好让「还没调用 set_mode 就想 import 主题」的场合
# （比如测试直接读常量）也有合理默认。必须放在 apply 之后，
# 因为 set_mode 依赖 _LIGHT/_ALIASES 已定义。
set_mode("light")
