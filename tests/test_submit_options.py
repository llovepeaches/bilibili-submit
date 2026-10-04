"""投稿选项：杜比 / Hi-Res / 互动设置。

这几个开关要穿过四层才到得了 B 站：界面 → 配置（``TaskConfig``）→
元数据（``ArchiveMeta``）→ payload。任何一层漏掉或改名写错，表现都是
同一个——**开关点了没反应**，不报错也不告警，是最难查的一类问题。
所以这里按链路逐层测，而不是各层分开测。

尤其 ``hires`` → ``lossless_music`` 这处改名：B 站官方文档里 Hi-Res 的
字段名就叫 ``lossless_music``（不叫 hires），写错名字接口会静默忽略。
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bilibili_submit import cli  # noqa: E402
from bilibili_submit.config import (  # noqa: E402
    AppConfig,
    DefaultsConfig,
    TaskConfig,
    load_config,
)
from bilibili_submit.metadata import ArchiveMeta, build_payload  # noqa: E402
from bilibili_submit.scheduler import TaskOutcome, build_archive_meta  # noqa: E402


def _meta(**kwargs) -> ArchiveMeta:
    return ArchiveMeta(
        title="测试稿件",
        videos=[{"filename": "f1", "title": "P1"}],
        **kwargs,
    )


# ---------- payload：发给 B 站的最终形态 ----------


def test_audio_flags_are_always_present_even_when_off():
    """杜比/Hi-Res 关着也要出现在 payload 里。

    官方参数表把这两个标成「必要」，默认值就是 0。只在开启时才写的话，
    关着的时候服务端收不到字段——现在多数情况没事，但哪天它改成读默认
    值就会出问题，而且这种问题只在B站改接口的那天爆发。
    """
    payload = build_payload(_meta(), csrf="x")
    assert payload["dolby"] == 0
    assert payload["lossless_music"] == 0


def test_enabled_audio_flags_reach_the_payload():
    payload = build_payload(_meta(dolby=1, lossless_music=1), csrf="x")
    assert payload["dolby"] == 1
    assert payload["lossless_music"] == 1


def test_interaction_flags_reach_the_payload():
    payload = build_payload(
        _meta(
            up_close_reply=True,
            up_close_danmu=True,
            up_selection_reply=True,
        ),
        csrf="x",
    )
    assert payload["up_close_reply"] is True
    assert payload["up_close_danmu"] is True
    assert payload["up_selection_reply"] is True


def test_interaction_flags_stay_absent_when_off():
    """关着的互动开关不写进 payload，与已有的关闭评论/弹幕保持一致。"""
    payload = build_payload(_meta(), csrf="x")
    assert "up_close_reply" not in payload
    assert "up_close_danmu" not in payload
    assert "up_selection_reply" not in payload


def test_normalized_coerces_truthy_audio_flags():
    """配置里写 true 也要变成接口认的 1。

    字符串交给配置层的 ``_as_flag`` 去解析，元数据层只认程序给的
    bool / int——两层职责分开，宽容解析只有一处。
    """
    meta = _meta(dolby=True, lossless_music=1).normalized()
    assert meta.dolby == 1
    assert meta.lossless_music == 1
    assert isinstance(meta.dolby, int)


# ---------- 配置 → 元数据：改名发生在这里 ----------


def test_hires_becomes_lossless_music():
    """配置说 hires，接口要 lossless_music——这处改名必须被直接测到。

    写错成 ``hires=1`` 直接塞进 payload 的话，B 站不认识这个字段，
    既不报错也不生效，用户会以为「Hi-Res 开了没效果」。
    """
    meta = build_archive_meta(TaskConfig(hires=1, title="t"), [])
    assert meta.lossless_music == 1
    assert meta.dolby == 0


def test_dolby_reaches_the_meta():
    meta = build_archive_meta(TaskConfig(dolby=1, title="t"), [])
    assert meta.dolby == 1
    assert meta.lossless_music == 0


def test_interaction_flags_reach_the_meta():
    meta = build_archive_meta(
        TaskConfig(
            title="t",
            up_close_reply=True,
            up_close_danmu=True,
            up_selection_reply=True,
        ),
        [],
    )
    assert meta.up_close_reply is True
    assert meta.up_close_danmu is True
    assert meta.up_selection_reply is True


def test_unset_options_default_to_off():
    """任务没配这些项时全是关——投稿默认不该替用户改变互动设置。"""
    meta = build_archive_meta(TaskConfig(title="t"), [])
    assert (meta.dolby, meta.lossless_music) == (0, 0)
    assert (
        meta.up_close_reply,
        meta.up_close_danmu,
        meta.up_selection_reply,
    ) == (False, False, False)


# ---------- 配置层：默认值与逐任务覆盖 ----------


def test_defaults_normalizes_truthy_values():
    """yaml 里写 "1" / "yes" 都认，写 "0" / "false" 要真的关掉。

    ``bool("0")`` 是 True——直接转 bool 会把「关」读成「开」，这种错误
    只在用户明确想关掉某一项时才暴露，而且方向正好相反。
    """
    cfg = DefaultsConfig(dolby="1", hires="yes", up_close_reply=1)
    assert cfg.dolby == 1
    assert cfg.hires == 1
    assert cfg.up_close_reply is True

    off = DefaultsConfig(dolby="0", hires="false", up_close_reply="no")
    assert off.dolby == 0
    assert off.hires == 0
    assert off.up_close_reply is False


def test_task_none_falls_back_to_defaults():
    task = TaskConfig(name="t").merged(
        DefaultsConfig(dolby=1, hires=1, up_close_reply=True)
    )
    assert task.dolby == 1
    assert task.hires == 1
    assert task.up_close_reply is True


def test_task_can_explicitly_turn_off_what_defaults_turned_on():
    """defaults 开着、单任务想关掉：写 false / 0 必须生效。

    ``merged`` 用 ``is None`` 判断「没配」，所以显式给的 ``False`` / ``0``
    不能被当成没配、再被默认值覆盖回去——否则「这一批都开杜比，
    就这一个不开」根本表达不出来。
    """
    task = TaskConfig(dolby=0, hires=0, up_close_reply=False).merged(
        DefaultsConfig(dolby=1, hires=1, up_close_reply=True)
    )
    assert task.dolby == 0
    assert task.hires == 0
    assert task.up_close_reply is False


def test_yaml_accepts_the_new_options(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text(
        "defaults:\n"
        "  dolby: 1\n"
        "  hires: 1\n"
        "  up_close_reply: true\n"
        "  up_close_danmu: true\n"
        "  up_selection_reply: true\n"
        "tasks:\n"
        "  - name: t\n"
        "    type: single\n"
        "    file: a.mp4\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.defaults.dolby == 1
    assert cfg.defaults.hires == 1
    assert cfg.defaults.up_selection_reply is True


def test_yaml_rejects_unknown_option_names(tmp_path):
    """拼错的字段名要报错，不能静默忽略。

    把 ``hires`` 写成 ``hires_audio`` 之类的，如果静默忽略，用户会以为
    配上了——这正是「开关点了没反应」的源头。
    """
    path = tmp_path / "c.yaml"
    path.write_text(
        "defaults:\n  hires_audio: 1\n"
        "tasks:\n  - name: t\n    type: single\n    file: a.mp4\n",
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="未知字段"):
        load_config(path)


# ---------- 命令行 ----------


def _upload_with(monkeypatch, tmp_path, argv, config_text=None):
    """跑一次 upload 并抓住最终交给执行引擎的 task。"""
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")

    captured: dict = {}

    def fake_run_task(client, task, cfg, **_kwargs):
        captured["task"] = task
        return TaskOutcome(name=task.name, success=True, bvid="BV1")

    monkeypatch.setattr(cli, "run_task", fake_run_task)
    monkeypatch.setattr(cli, "_client_from_config", lambda cfg: object())

    args_list = ["upload", str(video), *argv]
    if config_text is not None:
        cfg_path = tmp_path / "cfg.yaml"
        cfg_path.write_text(config_text, encoding="utf-8")
        args_list += ["-c", str(cfg_path)]

    args = cli.build_parser().parse_args(args_list)
    cli.cmd_upload(args)
    return captured["task"]


def test_cli_flags_reach_the_task(monkeypatch, tmp_path):
    task = _upload_with(
        monkeypatch,
        tmp_path,
        ["--dolby", "--hires", "--close-reply", "--close-danmu", "--selection-reply"],
    )
    assert task.dolby == 1
    assert task.hires == 1
    assert task.up_close_reply is True
    assert task.up_close_danmu is True
    assert task.up_selection_reply is True


def test_cli_flags_stay_off_when_not_given(monkeypatch, tmp_path):
    """没传开关就是「不指定」，不该变成关——否则会盖掉配置文件里的开启。"""
    task = _upload_with(
        monkeypatch,
        tmp_path,
        [],
        config_text=(
            "defaults:\n  dolby: 1\n  hires: 1\n"
            "tasks:\n  - name: t\n    type: single\n    file: a.mp4\n"
        ),
    )
    assert task.dolby == 1
    assert task.hires == 1


def test_cli_unspecified_flags_fall_back_to_off(monkeypatch, tmp_path):
    """配置文件也没提时，默认全关。"""
    task = _upload_with(monkeypatch, tmp_path, [])
    assert task.dolby == 0
    assert task.hires == 0
    assert task.up_close_reply is False
