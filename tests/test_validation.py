"""共享校验模块测试（B1 / B3 的守门）。

这个模块存在的理由就是「两页各写一遍校验，必然写歪」——单文件页拦了
超长标题，批量页不拦，用户整批投完才被服务端逐条打回（21015 / 21018）。
规则收口到一处之后，这里逐条钉住：

- 上限（标题 80 / 简介 2000 / 标签 10）：边界值必须**过**、超一格必须**抛**，
  且错误信息里要带上当前值——只说「超限了」用户不知道超多少。
- 定时发布只有**下限**（距今至少 4 小时，与 ``resolve_dtime`` 对齐），
  没有界面需要关心的上限——这条是审计后与平台规则核对过的决定，
  别看到「无上限校验」就以为漏了，加一条上限测试只会把决定焊死错。
- ``parse_offset`` 的错误信息要能引导用户改输入，而不是只报异常类型。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.exceptions import BiliError  # noqa: E402
from bilibili_submit.ui.validation import (  # noqa: E402
    MAX_DESC,
    MAX_TAGS,
    MAX_TITLE,
    MIN_OFFSET_HOURS,
    parse_offset,
    validate_desc,
    validate_offset_hours,
    validate_tag_list,
    validate_tag_text,
    validate_title,
)


def test_title_at_limit_passes_and_one_over_is_rejected():
    """标题 80 字是平台硬限制：刚好 80 必须放过，81 必须拦。"""
    validate_title("标" * MAX_TITLE)  # 不抛即过

    with pytest.raises(BiliError) as exc:
        validate_title("标" * (MAX_TITLE + 1))
    message = str(exc.value)
    assert str(MAX_TITLE) in message, f"错误信息应告知上限：{message!r}"
    assert str(MAX_TITLE + 1) in message, f"错误信息应告知当前值：{message!r}"


def test_desc_at_limit_passes_and_one_over_is_rejected():
    """简介上限 2000 字，同样卡边界。"""
    validate_desc("简" * MAX_DESC)

    with pytest.raises(BiliError) as exc:
        validate_desc("简" * (MAX_DESC + 1))
    assert str(MAX_DESC) in str(exc.value)


def test_tag_list_count_limit():
    """标签个数上限 10：10 个放过，11 个抛。"""
    validate_tag_list([f"t{i}" for i in range(MAX_TAGS)])

    with pytest.raises(BiliError) as exc:
        validate_tag_list([f"t{i}" for i in range(MAX_TAGS + 1)])
    assert str(MAX_TAGS) in str(exc.value)


def test_tag_text_counts_mixed_separators():
    """批量页标签是自由文本：半角逗号、中文逗号、空白都算分隔。

    用户不会关心该用哪种逗号，数出来的个数必须和单文件页的列表一致，
    否则同一批标签在两页一个过一个不过。
    """
    validate_tag_text("标签一, 标签二，标签三  标签四")  # 4 个，不抛
    validate_tag_text("")  # 空串等于没填，不拦

    with pytest.raises(BiliError):
        # 11 个，混用三种分隔——少算一种都会漏过去
        validate_tag_text("，".join(f"t{i}" for i in range(MAX_TAGS + 1)))


def test_offset_only_has_lower_bound():
    """定时发布只校验下限：None 放过、不足 4 小时抛、4 小时整点过、大值也过。

    没有上限是**有意的**：平台只规定了「至少提前多久」，界面层再加个
    天花板只会把合法预约拦死。谁要加上限，先去看接口文档。
    """
    validate_offset_hours(None)  # 立即发布
    validate_offset_hours(MIN_OFFSET_HOURS)  # 边界值必须过
    validate_offset_hours(72.0)  # 三天后发布，平台允许

    with pytest.raises(BiliError) as exc:
        validate_offset_hours(MIN_OFFSET_HOURS - 0.1)
    assert "4" in str(exc.value), f"应提示至少 4 小时：{exc.value!r}"


def test_parse_offset_reads_text():
    """空串→立即发布；数字串→小时数；非数字与不足下限都要抛。"""
    assert parse_offset("") is None
    assert parse_offset("   ") is None
    assert parse_offset(" 6 ") == 6.0  # 前后空白要裁掉，用户会手滑

    with pytest.raises(BiliError) as exc:
        parse_offset("六")
    assert "不是数字" in str(exc.value), f"非数字要说清问题：{exc.value!r}"

    with pytest.raises(BiliError):
        parse_offset("2")  # 不足下限——提交时必被 resolve_dtime 拦，不如先拦
