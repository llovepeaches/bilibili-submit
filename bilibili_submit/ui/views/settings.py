"""设置视图：代理、cookie 路径、ffmpeg 自检。

改动即时写入 :class:`AppContext`，各视图下次操作时自动生效，
不需要「保存并重启」。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from ...ffmpeg import ffmpeg_status, ffmpeg_version
from .. import theme
from ..widgets import (
    Card,
    FormRow,
    KeyValueList,
    SectionTitle,
    SecondaryButton,
)

__all__ = ["SettingsView"]


class SettingsView(ttk.Frame):
    """设置页。"""

    def __init__(self, master: tk.Misc, app: "object") -> None:
        super().__init__(master, style="TFrame")
        self.app = app
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build()

    def _build(self) -> None:
        SectionTitle(
            self, "设置", "改动立即生效，无需重启。"
        ).grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_MD))

        card = Card(self)
        card.grid(row=1, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)

        # 代理
        self._proxy_var = tk.StringVar()
        row = FormRow(
            card,
            "代理",
            hint="例如 http://127.0.0.1:7890；境外网络或直连被风控时填。留空表示不走代理。",
        )
        row.grid(row=0, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        row.add(ttk.Entry, textvariable=self._proxy_var)
        self._proxy_var.trace_add("write", self._on_proxy_change)

        # cookie 文件
        self._cookie_var = tk.StringVar()
        row = FormRow(
            card, "Cookie 文件", hint="含 SESSDATA，等同于账号凭据，请勿外传。"
        )
        row.grid(row=1, column=0, sticky="ew", pady=(0, theme.PAD_SM))
        row.add(ttk.Entry, textvariable=self._cookie_var, padx=(0, theme.PAD_SM))
        row.add(
            SecondaryButton, text="浏览…", command=self._pick_cookie, column=1, sticky="w"
        )

        ttk.Separator(card, orient="horizontal").grid(
            row=2, column=0, sticky="ew", pady=theme.PAD_MD
        )

        # 环境自检
        ttk.Label(card, text="环境自检", style="Heading.TLabel").grid(
            row=3, column=0, sticky="w", pady=(0, theme.PAD_SM)
        )
        self._env = KeyValueList(card, label_width=12)
        self._env.grid(row=4, column=0, sticky="w")

    # ---------- 行为 ----------

    def refresh(self) -> None:
        """切到本页时用当前设置回填表单，并跑一次环境自检。"""
        ctx = self.app.ctx
        if not self._proxy_var.get():
            self._proxy_var.set(ctx.proxy or "")
        if not self._cookie_var.get():
            self._cookie_var.set(ctx.cookie_file)
        self._fill_env()

    def _on_proxy_change(self, *_: "object") -> None:
        value = self._proxy_var.get().strip()
        self.app.ctx.proxy = value or None

    def _pick_cookie(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Cookie 保存位置",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if path:
            self._cookie_var.set(path)
            self.app.ctx.cookie_file = path
            self.app.refresh_status()

    def _fill_env(self) -> None:
        ctx = self.app.ctx
        rows = [
            (
                "登录态",
                "已登录" if ctx.logged_in else "未登录",
                "ok" if ctx.logged_in else "warn",
            ),
            (
                "配置文件",
                ctx.config_path or "未加载",
                "ok" if ctx.config_path else "idle",
            ),
            ("代理", ctx.proxy or "未设置", "ok" if ctx.proxy else "idle"),
        ]

        info = ffmpeg_status()
        if info is None:
            rows.append(("ffmpeg", "未找到（仅影响自动抽帧）", "warn"))
        else:
            version = ffmpeg_version(info)
            if version != "未知":
                version = version.replace("ffmpeg version ", "").split(" ")[0]
            rows.append((f"ffmpeg（{info.source}）", version, "ok"))

        self._env.set_rows(rows)
