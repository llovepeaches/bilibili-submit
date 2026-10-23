"""Web 版测试。

Web 端是桌面端的第二个调用方，所以这里最值钱的几条用例测的不是
「接口返回什么」，而是**两边不会悄悄分叉**：

- :class:`~bilibili_submit.web.events.Event` 与
  :class:`~bilibili_submit.ui.workers.Event` 必须逐字段一致；
- Web 不许直接碰上传，也不许碰 tkinter；
- 目录浏览的三条硬约束。

Flask 是软依赖，用 ``pytest.importorskip`` 挡住——不装的时候是跳过
而不是失败。CI 里 ``requirements-dev.txt`` 会装上它。
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from bilibili_submit.exceptions import ConfigError
from bilibili_submit.web import browse, events, jobs as jobs_mod
from bilibili_submit.web.submit import (
    CommonFields,
    TaskDraft,
    build_preview,
    drafts_from_payload,
    environment_snapshot,
    run_submit,
    selected_pairs,
)

ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = ROOT / "bilibili_submit" / "web"

# ---------------------------------------------------------------------------
# 事件契约：与桌面端逐字段比对
# ---------------------------------------------------------------------------


def _dataclass_fields(source: str, class_name: str = "Event") -> list[tuple[str, str]]:
    """从源码里抠出一个 dataclass 的字段名与默认值（AST，不 import）。

    为什么不 import：``ui/workers.py`` 顶部就 ``import tkinter``，
    而 Web 端的整个立身之本是「没装 tkinter 也能跑」。为了测契约去
    import tkinter，等于让测试环境决定这条守卫能不能存在。
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == class_name):
            continue
        out: list[tuple[str, str]] = []
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                default = "" if stmt.value is None else ast.unparse(stmt.value)
                out.append((stmt.target.id, default))
        return out
    raise AssertionError(f"源码里找不到 class {class_name}")


def test_web_event_matches_ui_event():
    """Web 的 Event 必须和桌面端的**逐字段**一致。

    它是复制出来的（见 ``web/events.py`` 的注释），而复制的东西一定会
    漂移：哪天给桌面端加个字段，Web 端会静默少一半状态，界面上表现为
    「进度条不动但任务其实在跑」——没有这条用例，谁也想不到去看这里。
    """
    ui_fields = _dataclass_fields(
        (ROOT / "bilibili_submit" / "ui" / "workers.py").read_text(encoding="utf-8")
    )
    web_fields = _dataclass_fields(
        (WEB_ROOT / "events.py").read_text(encoding="utf-8")
    )
    assert web_fields, "web/events.py 里没解析到 Event 的字段（改名了？）"
    assert web_fields == ui_fields, (
        "web Event 与 ui Event 的字段不一致：\n"
        f"  ui : {ui_fields}\n"
        f"  web: {web_fields}\n"
        "两边都要改——这个类是刻意复制的，改一边忘另一边就会静默丢状态。"
    )


def test_event_percent_uses_position_not_index():
    """``index`` 和 ``position`` 混用会算出 200%，这是踩过的坑。

    只勾下标 3 和 5 时 ``total=2``，拿 ``index+1`` 当分子就顶满进度条。
    """
    event = events.Event("done", index=5, position=2, total=2)
    assert event.percent == 100.0
    # position 为 0（调用方没给）时退回 0，而不是拿 index 顶替
    assert events.Event("done", index=5, total=2).percent == 0.0


# ---------------------------------------------------------------------------
# 分层：Web 不许碰上传，也不许碰 tkinter
# ---------------------------------------------------------------------------


def test_web_never_imports_tkinter():
    """Web 端整包不许出现 tkinter。

    Web 版的立身之本就是「没装 tkinter 也能用」——桌面版在精简 Python
    上打不开，Web 版正是那条退路。一旦某个模块顺手 import 了 tkinter，
    这条退路就没了，而且只会在**没装 tkinter 的机器上**暴露，
    开发机上永远试不出来。
    """
    bad: list[str] = []
    for path in sorted(WEB_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            for name in names:
                if name.split(".")[0] == "tkinter":
                    bad.append(f"{path.name}:{node.lineno}  {name}")
    assert not bad, (
        "Web 端不该依赖 tkinter（没有它正是 Web 版存在的理由）：\n"
        + "\n".join(f"  {item}" for item in bad)
    )


def test_web_never_reaches_for_upload():
    """Web 不许直接调 ``upload_video`` 或 import 上传模块。

    上传的重试、续传、错误归类全在 ``scheduler`` 里。Web 自己调一次
    ``upload_video`` 等于把这套逻辑复制一份还不带重试，而那条路径
    没有任何单测会覆盖到。
    """
    bad: list[str] = []
    for path in sorted(WEB_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                "upload"
            ):
                if "bilibili_submit" in ast.unparse(node):
                    bad.append(f"{path.name}:{node.lineno}  {ast.unparse(node)}")
            if isinstance(node, ast.Name) and node.id == "upload_video":
                bad.append(f"{path.name}:{node.lineno}  upload_video")
    assert not bad, (
        "Web 端不该直接碰上传：\n"
        + "\n".join(f"  {item}" for item in bad)
        + "\n\n投稿一律走 scheduler.run_all。"
    )


# ---------------------------------------------------------------------------
# 目录浏览的三条硬约束
# ---------------------------------------------------------------------------


def test_browse_rejects_pseudo_filesystems():
    """``/proc`` ``/sys`` ``/dev`` 不许列。

    这些目录里的「文件」大小是假的、读起来可能永不返回。把它们列出来
    界面就卡死了，而且用户点进 ``/proc/self`` 能看到进程信息。
    """
    for path in ("/proc", "/sys", "/dev"):
        with pytest.raises(ConfigError):
            browse.reject_path(path)


def test_browse_resolves_dot_dot_and_symlinks(tmp_path):
    """``..`` 与 symlink 都要在 realpath 之后判。

    只看字符串的话 ``/tmp/../etc`` 看着像在 /tmp 底下，
    symlink 更是完全看不出来——必须解析完再判断。
    """
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    assert browse.reject_path(str(link)) == real
    assert browse.reject_path(f"{tmp_path}/real/../real") == real


def test_browse_rejects_non_directory(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(ConfigError):
        browse.reject_path(str(missing))

    a_file = tmp_path / "a.mp4"
    a_file.write_bytes(b"0")
    with pytest.raises(ConfigError):
        browse.reject_path(str(a_file))


def test_browse_lists_only_dirs_and_videos(tmp_path):
    """只列子目录与视频文件——不列 ``id_rsa`` 这类无关文件。"""
    (tmp_path / "sub").mkdir()
    (tmp_path / "clip.mp4").write_bytes(b"0")
    (tmp_path / "notes.txt").write_text("secret", encoding="utf-8")

    result = browse.browse(str(tmp_path))
    assert [d["name"] for d in result["dirs"]] == ["sub"]
    assert [f["name"] for f in result["files"]] == ["clip.mp4"]


def test_browse_truncates_and_says_so(tmp_path, monkeypatch):
    """截断必须告诉前端「还有更多」，不能悄悄少给。

    静默截断会被当成 bug——用户数了下目录里有 3000 个视频，界面上
    只显示 2000 个，第一反应是程序坏了，而不是「它有意这么做」。
    """
    monkeypatch.setattr(browse, "MAX_LISTED_FILES", 3)
    for index in range(5):
        (tmp_path / f"v{index}.mp4").write_bytes(b"0")

    result = browse.browse(str(tmp_path))
    assert len(result["files"]) == 3
    assert result["truncated"] is True


# ---------------------------------------------------------------------------
# 预览与任务
# ---------------------------------------------------------------------------


def _videos(tmp_path: Path) -> Path:
    root = tmp_path / "videos"
    (root / "旅行").mkdir(parents=True)
    for name in ("a.mp4", "b.mp4", "旅行/p1.mp4", "旅行/p2.mp4"):
        (root / name).write_bytes(b"0")
    return root


def test_build_preview_none_gives_one_file_per_archive(tmp_path):
    drafts = build_preview(str(_videos(tmp_path)), "none")
    assert [d.name for d in drafts] == ["a", "b"]


def test_build_preview_folder_merges_subdir(tmp_path):
    """按文件夹分组要多扫一层——不钻进去就看不到目录结构。"""
    drafts = build_preview(str(_videos(tmp_path)), "folder")
    names = {d.name: len(d.files) for d in drafts}
    assert names["旅行"] == 2


def test_build_preview_rejects_empty_dir(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ConfigError):
        build_preview(str(empty), "none")


def test_drafts_from_payload_reports_missing_files(tmp_path):
    """文件存在性必须在服务端查——前端给的只是上次预览的快照。"""
    with pytest.raises(ConfigError) as exc:
        drafts_from_payload([{"name": "x", "files": [str(tmp_path / "gone.mp4")]}])
    assert "不存在" in str(exc.value)


def test_selected_pairs_keeps_the_index_in_the_full_list():
    """过滤勾选时不能丢下标——事件要靠它指回预览列表的行。

    只回传勾选的话，行号会错位：勾了第 2、3 个，界面上却把第 1、2 行
    标成成功。这正是桌面端踩过、并在 ``Event`` 里把 ``index`` 和
    ``position`` 分开的原因。
    """
    drafts = [
        TaskDraft(name="a", files=["/x/a.mp4"], selected=False),
        TaskDraft(name="b", files=["/x/b.mp4"]),
        TaskDraft(name="c", files=["/x/c.mp4"]),
    ]
    pairs = selected_pairs(drafts)
    assert [index for index, _ in pairs] == [1, 2]
    assert [draft.name for _i, draft in pairs] == ["b", "c"]


# ---------------------------------------------------------------------------
# Job：同一时刻只允许一个
# ---------------------------------------------------------------------------


def test_job_manager_rejects_a_second_concurrent_job():
    """两个投稿同时跑会互相覆盖 cookie 与历史文件，必须挡住。"""
    gate = {"stop": False}
    manager = jobs_mod.JobManager()

    def hold(job: jobs_mod.Job) -> None:
        while not gate["stop"]:
            time.sleep(0.01)
        job.finish("done")

    manager.start(hold)
    with pytest.raises(jobs_mod.JobBusy):
        manager.start(hold)
    gate["stop"] = True


def test_job_finish_emits_an_end_marker():
    """必须有一个 ``end`` 事件，否则 SSE 端会一直挂着等。

    浏览器靠这条判定「结束了」，没有它界面会永远转圈。
    """
    job = jobs_mod.Job(id="x", total=1)
    job.finish("done")
    payload = job.events.get(timeout=1)
    assert payload["type"] == "end"
    assert payload["state"] == "done"


def test_runner_exception_is_reported_not_swallowed():
    """线程里抛出去了，浏览器永远等不到 end——必须转成 error 状态。"""
    manager = jobs_mod.JobManager()

    def boom(_job: jobs_mod.Job) -> None:
        raise ValueError("炸了")

    job = manager.start(boom)
    deadline = time.time() + 5
    while job.state == "running" and time.time() < deadline:
        time.sleep(0.02)
    assert job.state == "error"
    assert "炸了" in job.error


# ---------------------------------------------------------------------------
# 执行：dry-run 与校验
# ---------------------------------------------------------------------------


def _drain(job: jobs_mod.Job) -> list[dict]:
    out: list[dict] = []
    while True:
        try:
            payload = job.events.get(timeout=5)
        except Exception:  # noqa: BLE001 - queue.Empty
            break
        out.append(payload)
        if payload.get("type") == "end":
            break
    return out


def test_dry_run_emits_events_with_full_list_indices(tmp_path):
    """事件里的 ``index`` 指回完整列表的行号，position 才是第几个。"""
    root = _videos(tmp_path)
    drafts = build_preview(str(root), "none")
    drafts[0].selected = False

    job = jobs_mod.Job(id="t", total=0)
    run_submit(job, drafts, CommonFields(), cookie_file=str(tmp_path / "c.json"),
               dry_run=True)
    events_seen = [p for p in _drain(job) if p.get("type") == "event"]

    dones = [p for p in events_seen if p["kind"] == "done"]
    assert [p["index"] for p in dones] == [1]
    assert [p["position"] for p in dones] == [1]
    assert dones[0]["total"] == 1
    assert job.state == "done"


def test_run_submit_rejects_overlong_title_before_touching_the_network(tmp_path):
    """超长标题要在入口拦住，不能等服务端打回。

    依赖 ``metadata`` 的静默截断更糟——用户不知道自己的标题被改过。
    """
    root = _videos(tmp_path)
    drafts = build_preview(str(root), "none")
    drafts[0].title = "标" * 81

    job = jobs_mod.Job(id="t", total=1)
    run_submit(job, drafts, CommonFields(), cookie_file=str(tmp_path / "c.json"))
    assert job.state == "error"
    assert "80" in job.error


def test_run_submit_needs_a_login(tmp_path):
    """没 cookie 时要说「未登录」，而不是抛一串 traceback 到日志里。"""
    drafts = build_preview(str(_videos(tmp_path)), "none")
    job = jobs_mod.Job(id="t", total=1)
    run_submit(job, drafts, CommonFields(), cookie_file=str(tmp_path / "nope.json"))
    assert job.state == "error"
    assert "未登录" in job.error


def test_environment_snapshot_reports_login_and_ffmpeg(tmp_path):
    snap = environment_snapshot(str(tmp_path / "nope.json"))
    assert snap["logged_in"] is False
    assert snap["tids"], "首页的分区下拉框要靠它填充"


# ---------------------------------------------------------------------------
# Flask 应用
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    flask = pytest.importorskip("flask")
    del flask  # 只为触发跳过
    from bilibili_submit.web.app import create_app

    app = create_app(cookie_file=str(tmp_path / "cookie.json"))
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def test_index_serves_the_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "bilibili-submit" in resp.get_data(as_text=True)


def test_api_status_reports_environment(client):
    data = client.get("/api/status").get_json()
    assert data["ok"] is True
    assert "logged_in" in data and data["tids"]


def test_api_browse_returns_a_clean_error_for_bad_paths(client):
    resp = client.get("/api/browse?path=/proc")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_api_preview_and_dry_run_job(client, tmp_path):
    root = _videos(tmp_path)
    resp = client.post("/api/preview", json={"dir": str(root), "group_by": "none"})
    tasks = resp.get_json()["tasks"]
    assert len(tasks) == 2

    created = client.post("/api/jobs", json={
        "tasks": tasks, "common": {"tid": 21}, "dry_run": True,
    })
    assert created.status_code == 200
    job_id = created.get_json()["job"]["id"]

    # 直接读快照：dry-run 很快，事件可能已经被取走
    deadline = time.time() + 5
    while time.time() < deadline:
        snap = client.get(f"/api/jobs/{job_id}").get_json()["job"]
        if snap["state"] != "running":
            break
        time.sleep(0.02)
    assert snap["state"] == "done"


def test_api_jobs_rejects_an_empty_selection(client):
    resp = client.post("/api/jobs", json={"tasks": [], "common": {}})
    assert resp.status_code == 400


def test_api_unknown_common_field_is_rejected(client, tmp_path):
    """未知字段要报错，不能静默忽略。

    静默忽略的表现是「我明明填了 xx 却没生效」，而日志里一切正常——
    最难排查的那一类。
    """
    root = _videos(tmp_path)
    tasks = client.post("/api/preview", json={"dir": str(root)}).get_json()["tasks"]
    resp = client.post("/api/jobs", json={
        "tasks": tasks, "common": {"typo_field": 1},
    })
    assert resp.status_code == 400
    assert "typo_field" in resp.get_json()["error"]


def test_api_missing_job_is_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/jobs/nope/cancel").status_code == 404


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------


def test_cli_exposes_the_web_subcommand():
    from bilibili_submit.cli import build_parser

    args = build_parser().parse_args(["web", "--port", "9000", "--no-browser"])
    assert args.command == "web"
    assert args.port == 9000
    assert args.no_browser is True
    # 默认值必须是只听本机——换成 0.0.0.0 等于把投稿权限给整个网段
    assert build_parser().parse_args(["web"]).host == "127.0.0.1"


def test_cli_web_tells_you_how_to_install_flask(capsys, monkeypatch):
    """缺 Flask 时要给出可操作的提示，不是一串 import traceback。"""
    import bilibili_submit.web as web_pkg
    from bilibili_submit import cli

    monkeypatch.setattr(web_pkg, "web_available", lambda: (False, "未安装 Flask（测试）"))

    assert cli.main(["web"]) == cli.EXIT_FAIL
    out, err = capsys.readouterr()
    assert "flask" in (out + err).lower(), (
        "提示里要写清装什么——只说「无法启动」用户不知道下一步"
    )
