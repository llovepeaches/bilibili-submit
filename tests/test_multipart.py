"""分 P 分组测试。

分组是**猜**用户意图，猜错了的代价是把两个不相干的视频投成同一个稿件的
两 P（要删稿重投）。所以这里重点测的是**不该合并的情况**，
以及"看起来像一套"的各种命名写法确实能并到一起。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit.multipart import (  # noqa: E402
    group_by_folder,
    group_by_prefix,
    group_files,
    natural_key,
    strip_part_marker,
)


def paths(directory: str, names: list[str]) -> list[Path]:
    return [Path(directory) / name for name in names]


def grouped(directory: str, names: list[str]):
    """分组结果简化成 {组名: [文件名]} 便于断言。"""
    return {
        group.name: [path.name for path in group.files]
        for group in group_by_prefix(paths(directory, names))
    }


# ---------- 序号识别 ----------


@pytest.mark.parametrize(
    "stem,expected",
    [
        ("旅行_01", ("旅行", 1)),
        ("旅行-2", ("旅行", 2)),
        ("旅行 10", ("旅行", 10)),
        ("vlog-1", ("vlog", 1)),
        ("P3", ("", 3)),
        ("part4", ("", 4)),
        ("ep05", ("", 5)),
        ("电影 (1)", ("电影", 1)),
        ("第3集", ("", 3)),
        ("教程_a", ("教程_a", None)),
        ("教程", ("教程", None)),
        # 中间的编号不是序号，不该被当成分 P 标记
        ("第3集 正片", ("第3集 正片", None)),
    ],
)
def test_strip_part_marker(stem, expected):
    assert strip_part_marker(stem) == expected


def test_resolution_suffix_is_stripped_before_number():
    """``_1080p`` 不剥掉的话，尾部是 p 不是数字，整批就分不成组。"""
    assert strip_part_marker("旅行_01_1080p") == ("旅行", 1)
    assert strip_part_marker("旅行_02_4k") == ("旅行", 2)


def test_natural_key_puts_p2_before_p10():
    """纯字典序会把 P10 排到 P2 前面，分 P 顺序就乱了。"""
    assert sorted(["P10", "P2", "P1"], key=natural_key) == ["P1", "P2", "P10"]
    assert sorted(["旅行_10", "旅行_2", "旅行_1"], key=natural_key) == [
        "旅行_1",
        "旅行_2",
        "旅行_10",
    ]


# ---------- 应该合并 ----------


def test_same_prefix_merges_into_one_group():
    result = grouped("/v/旅行", ["旅行_01.mp4", "旅行_02.mp4", "旅行_03.mp4"])
    assert result == {"旅行": ["旅行_01.mp4", "旅行_02.mp4", "旅行_03.mp4"]}


def test_mixed_separators_still_group_together():
    """一个用下划线一个用连字符，去掉序号后都是「旅行」。"""
    result = grouped("/v/旅行", ["旅行_01.mp4", "旅行-02.mp4"])
    assert result == {"旅行": ["旅行-02.mp4", "旅行_01.mp4"]}


def test_case_insensitive_prefix():
    result = grouped("/v/旅行", ["Vlog_1.mp4", "vlog_2.mp4"])
    assert result == {"Vlog": ["Vlog_1.mp4", "vlog_2.mp4"]}


def test_episode_style_grouped_and_ordered():
    result = grouped("/v/剧集", ["第3集.mp4", "第1集.mp4", "第2集.mp4"])
    # 组名取目录名（文件名只剩序号，没有别的信息可用）
    assert result == {"剧集": ["第1集.mp4", "第2集.mp4", "第3集.mp4"]}


def test_resolution_suffixed_series_merges():
    result = grouped("/v/旅行", ["旅行_01_1080p.mp4", "旅行_02_1080p.mp4"])
    assert result == {"旅行": ["旅行_01_1080p.mp4", "旅行_02_1080p.mp4"]}


def test_pure_numbers_starting_at_one_merge():
    result = grouped("/v/连载", ["01.mp4", "02.mp4"])
    assert result == {"连载": ["01.mp4", "02.mp4"]}


def test_groups_sorted_by_first_file():
    names = ["b_1.mp4", "a_2.mp4", "a_1.mp4", "b_2.mp4"]
    order = [
        group.name for group in group_by_prefix(paths("/v/x", names))
    ]
    assert order == ["a", "b"]


# ---------- 不该合并（重点） ----------


def test_year_like_names_are_not_merged():
    """2023/2024 也是"连续数字"，但显然不是一套视频。

    名字里除了数字没有别的信息，只能靠"从 1 开始"这条硬指标兜底。
    """
    result = grouped("/v/年会", ["2023.mp4", "2024.mp4"])
    assert result == {"2023": ["2023.mp4"], "2024": ["2024.mp4"]}


def test_missing_first_part_is_not_merged():
    """只有 P2/P3 也不合并：缺了 P1，说明这本来就不是完整一套。"""
    result = grouped("/v/连载", ["P2.mp4", "P3.mp4"])
    assert result == {"P2": ["P2.mp4"], "P3": ["P3.mp4"]}


def test_unrelated_files_stay_separate():
    result = grouped("/v/x", ["教程.mp4", "独奏.mp4", "混剪.mp4"])
    assert set(result) == {"教程", "独奏", "混剪"}
    assert all(len(v) == 1 for v in result.values())


def test_two_series_in_one_folder_stay_separate():
    result = grouped(
        "/v/x",
        ["旅行_01.mp4", "旅行_02.mp4", "教程_01.mp4", "教程_02.mp4"],
    )
    assert result == {
        "旅行": ["旅行_01.mp4", "旅行_02.mp4"],
        "教程": ["教程_01.mp4", "教程_02.mp4"],
    }


def test_single_file_group_name_is_its_stem():
    """单文件组用文件名当组名，不该拿目录名（列表里会冒出一堆同名行）。"""
    result = grouped("/v/年会", ["2023.mp4"])
    assert result == {"2023": ["2023.mp4"]}


# ---------- 结构性质 ----------


def test_empty_input_returns_empty_list():
    assert group_by_prefix([]) == []


def test_every_input_file_appears_exactly_once():
    names = ["a_1.mp4", "a_2.mp4", "b.mp4", "02.mp4", "01.mp4"]
    groups = group_by_prefix(paths("/v/x", names))
    flattened = [path.name for group in groups for path in group.files]
    assert sorted(flattened) == sorted(names)


def test_group_files_are_natural_sorted():
    group = next(
        g
        for g in group_by_prefix(paths("/v/x", ["s_10.mp4", "s_2.mp4", "s_1.mp4"]))
        if g.name == "s"
    )
    assert [p.name for p in group.files] == ["s_1.mp4", "s_2.mp4", "s_10.mp4"]


def test_is_single_flags_orphan_groups():
    groups = group_by_prefix(paths("/v/x", ["a_1.mp4", "a_2.mp4", "b.mp4"]))
    flags = {g.name: g.is_single for g in groups}
    assert flags == {"a": False, "b": True}


# ---------- 按文件夹分组 ----------


def test_folder_groups_each_subfolder_into_one_archive():
    """每个子文件夹合成一个稿件，组名取文件夹名。"""
    root = Path("/v/视频")
    files = [
        root / "旅行" / "a.mp4",
        root / "旅行" / "b.mp4",
        root / "旅行" / "c.mp4",
        root / "教程" / "x.mp4",
    ]
    result = {g.name: [p.name for p in g.files] for g in group_by_folder(files, root)}
    assert result == {"旅行": ["a.mp4", "b.mp4", "c.mp4"], "教程": ["x.mp4"]}


def test_folder_does_not_merge_root_level_strays():
    """根目录第一层的散落文件**不合并**。

    它们没被放进任何子文件夹，说明用户没把它们当成一套。硬按目录名合成
    一个稿件，就是把几个不相干的视频投成同一稿件的分 P——误合并要删稿
    重投，代价比"多占一个 av 号"高得多。
    """
    root = Path("/v/视频")
    files = [root / "solo1.mp4", root / "solo2.mp4", root / "旅行" / "a.mp4"]
    result = {g.name: [p.name for p in g.files] for g in group_by_folder(files, root)}
    assert result == {
        "solo1": ["solo1.mp4"],
        "solo2": ["solo2.mp4"],
        "旅行": ["a.mp4"],
    }, "根目录两个散落文件各自独立，没有被合成一个「视频」稿件"


def test_folder_group_keeps_natural_order_inside():
    """组内按自然序：P2 要排在 P10 前面。"""
    root = Path("/v/视频")
    files = [root / "旅行" / f"{n}.mp4" for n in ("10", "2", "1")]
    group = group_by_folder(files, root)[0]
    assert [p.name for p in group.files] == ["1.mp4", "2.mp4", "10.mp4"]


def test_folder_groups_are_ordered_by_folder_name():
    """组顺序按文件夹名，不按组内文件名。

    按文件名排的话，不同文件夹里同名的文件（旅行/01.mp4 与 教程/01.mp4）
    会互相干扰，排出来跟目录结构对不上。
    """
    root = Path("/v/视频")
    # 文件名顺序（旅行先）与文件夹名顺序（教程先）故意相反，
    # 这样按文件名排的写法一定会被这条抓到
    files = [root / "旅行" / "01.mp4", root / "教程" / "02.mp4"]
    names = [g.name for g in group_by_folder(files, root)]
    assert names == ["教程", "旅行"], (
        "组顺序应跟着目录结构走，而不是跟着组内文件名走"
    )


def test_folder_single_file_folder_still_named_after_folder():
    """子文件夹里只有一个文件，标题也取文件夹名。

    文件名常常是 output.mp4、video.mp4 这种没信息量的，
    而文件夹名是用户自己起的名字。
    """
    root = Path("/v/视频")
    files = [root / "教程" / "output.mp4"]
    group = group_by_folder(files, root)[0]
    assert group.name == "教程"
    assert group.is_single


def test_folder_without_root_treats_everything_as_grouped():
    """不传 root 时无法区分"散落"，全部按所在目录成组。"""
    files = [Path("/v/视频/a.mp4"), Path("/v/视频/b.mp4")]
    groups = group_by_folder(files)
    assert [(g.name, len(g.files)) for g in groups] == [("视频", 2)]


def test_folder_mode_ignores_deeper_levels_by_construction():
    """第三层的文件不会混进来——扫描深度决定了输入，这里只保证不误判。"""
    root = Path("/v/视频")
    files = [root / "旅行" / "a.mp4", root / "旅行" / "子目录" / "z.mp4"]
    groups = group_by_folder(files, root)
    # 输入里给了就会成组（过滤是扫描那一层的职责），但组名各取自直接父目录
    assert {g.name for g in groups} == {"旅行", "子目录"}


def test_group_files_dispatches_by_mode():
    root = Path("/v/视频")
    files = [root / "旅行" / "a.mp4", root / "旅行" / "b.mp4"]
    assert len(group_files(files, "folder", root)) == 1
    # 前缀模式下这两个名字没有共同前缀可剥，应各自成组
    assert len(group_files(files, "prefix", root)) == 2


def test_group_files_unknown_mode_falls_back_to_no_grouping():
    """不认识的模式退回"每个文件一组"，而不是抛异常。

    分组只是猜意图，猜不出来退回最保守的结果，比让整次扫描失败好。
    """
    root = Path("/v/视频")
    files = [root / "旅行" / "a.mp4", root / "旅行" / "b.mp4"]
    groups = group_files(files, "瞎写的", root)
    assert all(g.is_single for g in groups)
    assert len(groups) == 2


def test_group_files_every_file_appears_once():
    root = Path("/v/视频")
    files = [
        root / "solo.mp4",
        root / "旅行" / "a.mp4",
        root / "旅行" / "b.mp4",
        root / "教程" / "x.mp4",
    ]
    flattened = [p.name for g in group_files(files, "folder", root) for p in g.files]
    assert sorted(flattened) == sorted(p.name for p in files)
