"""Web 界面（本机单用户）。

    from bilibili_submit.web import launch
    launch(port=8765)

**定位**：给「不想装桌面环境、或者人在另一台机器上」的场景用。它只
监听 127.0.0.1，一份 cookie，不做多用户隔离与鉴权——也就是说它假设
**能访问这台机器的只有你一个人**。这不是偷懒，是把安全边界划清楚：
多用户隔离要靠账号体系、会话管理、权限模型，那一整套做下去 Web 版
就不是「顺手加一个」的量级了。想让局域网里别人也能用，请先想清楚
「别人」是谁：那时你需要的是反向代理 + 认证，不是把这里改成 0.0.0.0。

**为什么用 Flask**：

1. 核心层是**全同步**的（601 频控退避会 ``time.sleep`` 到 3600 秒），
   任何 async 框架都得先把它包进线程池，等于白绕一圈；
2. SSE 用同步 generator 表达最自然（``yield`` 一条事件），
   而异步框架里 SSE 和同步阻塞任务的配合反而别扭；
3. 纯 Python，没有 C 扩展要编译——Linux 上装起来不会卡在构建轮子；
4. 自带 Jinja2，模板不用另找依赖。

Flask 是**软依赖**：命令行和桌面版都不需要它。缺了要给出可操作的
提示，而不是一串 import traceback。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import __version__
from ..config import DEFAULT_COOKIE_FILE as CONFIG_COOKIE_FILE
from ..exceptions import ConfigError
from ..scheduler import DEFAULT_HISTORY_FILE, read_history_diagnose
from .browse import browse, default_root
from .jobs import Job, JobBusy, JobManager
from .login import LoginSession
from .qr import qr_svg
from .submit import (
    CommonFields,
    build_preview,
    drafts_from_payload,
    environment_snapshot,
    run_submit,
    selected_pairs,
)

__all__ = ["create_app", "launch", "web_available"]

#: 静态资源目录。刻意不用打包器的产物——前端只有三个文件，
#: 引入构建链（npm / vite / 打包缓存）换来的复杂度远超收益。
STATIC_DIR = Path(__file__).parent / "static"

#: 事件流里「没有新事件」时也要发一条心跳的间隔（秒）。
#: 中间的反向代理/浏览器常在几十秒无数据后把连接掐掉。
_SSE_HEARTBEAT = 15.0

#: SSE 单次连接的最长存活时间（秒）。
#: 浏览器断线会自己重连，服务端不设上限的话线程会一直挂着。
_SSE_MAX_AGE = 600.0

#: 登录会话在 ``sessions`` 里的键。只有一个——本机单用户。
SESSION_KEY = "current"


def web_available() -> tuple[bool, str]:
    """Web 界面能不能用，返回 ``(是否可用, 不可用时原因)``。"""
    try:
        import flask  # noqa: F401
    except ImportError as exc:
        return False, f"未安装 Flask（{exc}）"
    return True, ""


@dataclass
class _Ctx:
    """路由之间要共享的那点状态。

    用一个对象而不是让 ``create_app`` 靠闭包变量往下传，是因为路由被
    拆成了几组注册函数——闭包捕获十几个变量的长函数，复杂度会一路
    涨到 pylama 的阈值以上，而拆开之后每组都能一眼读完。
    """

    cookie_file: str
    proxy: str | None
    jobs: JobManager
    #: 单用户，登录会话只有一个槽位。做成字典是为了让路由能取到同一个
    #: 对象；每次开始时清掉上一个。
    sessions: dict[str, LoginSession] = field(default_factory=dict)

    def fail(self, message: str, status: int = 400):
        from flask import jsonify

        return jsonify({"ok": False, "error": message}), status


def create_app(cookie_file: str | None = None, proxy: str | None = None):
    """建一个 Flask 应用。

    Args:
        cookie_file: cookie 路径，默认与命令行一致。
        proxy: 代理地址，如 ``http://127.0.0.1:7890``。
    """
    from flask import Flask

    ctx = _Ctx(
        cookie_file=cookie_file or CONFIG_COOKIE_FILE,
        proxy=proxy,
        jobs=JobManager(),
    )
    app = Flask(__name__, static_folder=None)
    _register_pages(app)
    _register_environment(app, ctx)
    _register_login(app, ctx)
    _register_browse(app, ctx)
    _register_jobs(app, ctx)
    return app


def _register_pages(app) -> None:
    """页面与静态资源。前端没有构建链，发什么就是什么。"""

    @app.get("/")
    def index():
        from flask import send_file

        return send_file(STATIC_DIR / "index.html")

    @app.get("/static/<path:name>")
    def static_files(name: str):
        from flask import send_from_directory

        return send_from_directory(STATIC_DIR, name)


def _register_environment(app, ctx: _Ctx) -> None:
    @app.get("/api/status")
    def api_status():
        from flask import jsonify

        snap = environment_snapshot(ctx.cookie_file, ctx.proxy)
        snap["ok"] = True
        snap["version"] = __version__
        snap["busy"] = ctx.jobs.current is not None and ctx.jobs.current.state == "running"
        return jsonify(snap)

    @app.get("/api/history")
    def api_history():
        from flask import jsonify

        entries, error = read_history_diagnose(DEFAULT_HISTORY_FILE)
        return jsonify({"ok": True, "entries": entries[-200:], "error": error})


def _register_login(app, ctx: _Ctx) -> None:
    @app.post("/api/login/start")
    def api_login_start():
        """开一次扫码登录，返回二维码 SVG 与登录链接。"""
        from flask import jsonify

        session = LoginSession(ctx.cookie_file, ctx.proxy)
        status = session.start()
        if status["state"] == "failed":
            return ctx.fail(str(status["error"]), 502)

        ctx.sessions.clear()
        ctx.sessions[SESSION_KEY] = session
        payload = dict(status)
        payload["ok"] = True
        try:
            payload["svg"] = qr_svg(str(status["url"]))
        except RuntimeError as exc:
            # 没装 qrcode 不该让登录彻底不可用：给出链接，
            # 用户在手机上打开一样能登录。
            payload["svg"] = ""
            payload["warning"] = str(exc)
        return jsonify(payload)

    @app.get("/api/login/status")
    def api_login_status():
        from flask import jsonify

        session = ctx.sessions.get(SESSION_KEY)
        if session is None:
            return jsonify({"ok": True, "state": "idle", "message": "还没有开始登录"})
        status = session.poll()
        status["ok"] = True
        return jsonify(status)


def _register_browse(app, ctx: _Ctx) -> None:
    @app.get("/api/browse")
    def api_browse():
        from flask import jsonify, request

        raw = request.args.get("path") or default_root()
        try:
            return jsonify({"ok": True, **browse(raw)})
        except ConfigError as exc:
            return ctx.fail(str(exc))

    @app.post("/api/preview")
    def api_preview():
        from flask import jsonify, request

        body = request.get_json(silent=True) or {}
        try:
            drafts = build_preview(
                str(body.get("dir") or ""), str(body.get("group_by") or "none")
            )
        except ConfigError as exc:
            return ctx.fail(str(exc))
        return jsonify({"ok": True, "tasks": [d.to_dict() for d in drafts]})


def _register_jobs(app, ctx: _Ctx) -> None:
    @app.post("/api/jobs")
    def api_jobs_create():
        from flask import jsonify, request

        body = request.get_json(silent=True) or {}
        try:
            drafts = drafts_from_payload(list(body.get("tasks") or []))
            common = CommonFields.from_dict(body.get("common"))
        except ConfigError as exc:
            return ctx.fail(str(exc))

        picked = selected_pairs(drafts)
        if not picked:
            return ctx.fail("没有勾选任何任务")
        dry_run = bool(body.get("dry_run"))

        def runner(job: Job) -> None:
            run_submit(
                job,
                drafts,
                common,
                cookie_file=ctx.cookie_file,
                proxy=ctx.proxy,
                dry_run=dry_run,
            )

        try:
            job = ctx.jobs.start(runner, total=len(picked))
        except JobBusy as exc:
            return ctx.fail(str(exc), 409)
        return jsonify({"ok": True, "job": job.snapshot()})

    @app.get("/api/jobs/<job_id>")
    def api_jobs_get(job_id: str):
        from flask import jsonify

        job = ctx.jobs.get(job_id)
        if job is None:
            return ctx.fail("任务不存在", 404)
        return jsonify({"ok": True, "job": job.snapshot()})

    @app.post("/api/jobs/<job_id>/cancel")
    def api_jobs_cancel(job_id: str):
        from flask import jsonify

        if not ctx.jobs.cancel(job_id):
            return ctx.fail("任务不存在或已结束", 404)
        return jsonify({"ok": True})

    @app.get("/api/jobs/<job_id>/events")
    def api_jobs_events(job_id: str):
        """SSE 事件流。

        为什么用 SSE 而不是 WebSocket：这个方向只有「服务端推送进度」
        一件事，浏览器那边的交互全是普通 POST。SSE 走普通 HTTP，
        穿过反向代理不需要额外配置，断线浏览器还会自动重连。
        """
        from flask import Response

        job = ctx.jobs.get(job_id)
        if job is None:
            return ctx.fail("任务不存在", 404)

        def stream():
            started = time.time()
            while time.time() - started < _SSE_MAX_AGE:
                try:
                    payload = job.events.get(timeout=_SSE_HEARTBEAT)
                except Exception:  # noqa: BLE001 - queue.Empty 也走这里
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                if payload.get("type") == "end":
                    return

        return Response(
            stream(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # 不让 nginx 攒着不发
            },
        )


def launch(
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = True,
    cookie_file: str | None = None,
    proxy: str | None = None,
) -> int:
    """启动 Web 服务。

    Args:
        host: 监听地址。**默认只听本机**，见模块 docstring 里的安全说明。
        port: 端口。
        open_browser: 是否自动打开浏览器。无头环境（服务器、容器）里
            会失败，忽略了继续跑即可——地址已经打印出来了。
    """
    ok, reason = web_available()
    if not ok:
        raise RuntimeError(f"{reason}。安装：pip install flask")

    app = create_app(cookie_file=cookie_file, proxy=proxy)
    url = f"http://{host}:{port}/"
    print(f"bilibili-submit Web 界面: {url}")
    print("只监听本机，请勿在不可信的网络环境里改用 0.0.0.0。")
    print("按 Ctrl+C 结束。")

    if open_browser:
        _open_browser(url)

    # debug=False 是刻意的：开 debug 会启用 werkzeug 的调试器，
    # 那个调试器是**远程代码执行**——就算只听 127.0.0.1，
    # 本机上的任何进程（包括浏览器里的 JS？不，但同机其它用户可以）
    # 都能通过它执行任意代码。本地工具不需要它。
    app.run(host=host, port=port, debug=False, threaded=True)
    return 0


def _open_browser(url: str) -> None:
    """试着打开浏览器。失败就算了——地址已经打在终端上了。"""
    import webbrowser

    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 - 无头环境常见，不该影响服务启动
        pass
