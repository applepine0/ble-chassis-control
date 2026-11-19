"""字体设置窗口。"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable
from tkinter import ttk

from .theme import DEFAULT_MONO_FAMILY, DEFAULT_UI_FAMILY, FontSettings


class FontSettingsWindow(tk.Toplevel):
    """调整界面字体与等宽字体,应用后立即刷新控件。"""

    def __init__(
        self,
        master,
        *,
        settings: FontSettings,
        families: Iterable[str],
        on_apply: Callable[[FontSettings], FontSettings | None],
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self._on_apply = on_apply
        self._on_close_cb = on_close
        self._closed = False

        self.title("字体设置")
        self.geometry("620x460")
        self.minsize(540, 400)
        self.transient(master)

        self._families = sorted(set(families), key=str.lower)
        if not self._families:
            self._families = [settings.ui_family]
        if settings.ui_family not in self._families:
            self._families.append(settings.ui_family)
        if settings.mono_family not in self._families:
            self._families.append(settings.mono_family)
            self._families.sort(key=str.lower)

        self.ui_family_var = tk.StringVar(value=settings.ui_family)
        self.ui_size_var = tk.IntVar(value=settings.ui_size)
        self.mono_family_var = tk.StringVar(value=settings.mono_family)
        self.mono_size_var = tk.IntVar(value=settings.mono_size)
        self.status_var = tk.StringVar(value="")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        body = ttk.Frame(self, padding=12)
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)

        self._build_ui(body, settings)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _e: self.close())

    # ------------------------------------------------------------------
    # 布局
    # ------------------------------------------------------------------
    def _build_ui(self, body: ttk.Frame, settings: FontSettings) -> None:
        ui_box = ttk.LabelFrame(body, text=" 界面字体 ", padding=8)
        ui_box.grid(row=0, column=0, sticky="ew")
        ui_box.columnconfigure(1, weight=1)

        ttk.Label(ui_box, text="字体").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(
            ui_box,
            textvariable=self.ui_family_var,
            values=self._families,
            state="readonly",
            width=30,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2)

        ttk.Label(ui_box, text="字号").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Spinbox(
            ui_box,
            from_=6,
            to=40,
            textvariable=self.ui_size_var,
            width=8,
        ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=2)

        mono_box = ttk.LabelFrame(body, text=" 等宽字体(日志 / 曲线) ", padding=8)
        mono_box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        mono_box.columnconfigure(1, weight=1)

        ttk.Label(mono_box, text="字体").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Combobox(
            mono_box,
            textvariable=self.mono_family_var,
            values=self._families,
            state="readonly",
            width=30,
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=2)

        ttk.Label(mono_box, text="字号").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Spinbox(
            mono_box,
            from_=6,
            to=40,
            textvariable=self.mono_size_var,
            width=8,
        ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=2)

        preview_box = ttk.LabelFrame(body, text=" 预览 ", padding=8)
        preview_box.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        preview_box.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        self.preview_ui = ttk.Label(
            preview_box,
            text="界面字体预览:底盘上位机 Aa 123",
            font=(settings.ui_family, settings.ui_size),
        )
        self.preview_ui.grid(row=0, column=0, sticky="w", pady=2)
        self.preview_mono = ttk.Label(
            preview_box,
            text="日志/曲线:VEL:600,0,0  RPM +123 -456",
            font=(settings.mono_family, settings.mono_size),
        )
        self.preview_mono.grid(row=1, column=0, sticky="w", pady=2)

        bar = ttk.Frame(body)
        bar.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(bar, textvariable=self.status_var).pack(side="left")
        ttk.Button(bar, text="恢复默认", command=self._reset).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="关闭", command=self.close).pack(side="right", padx=(6, 0))
        ttk.Button(bar, text="应用", command=self._apply).pack(side="right")

    # ------------------------------------------------------------------
    # 行为
    # ------------------------------------------------------------------
    def _collect(self) -> FontSettings | None:
        try:
            ui_size = int(self.ui_size_var.get())
            mono_size = int(self.mono_size_var.get())
        except (tk.TclError, ValueError):
            self.status_var.set("字号必须是整数")
            return None
        return FontSettings(
            ui_family=self.ui_family_var.get().strip() or DEFAULT_UI_FAMILY,
            ui_size=ui_size,
            mono_family=self.mono_family_var.get().strip() or DEFAULT_MONO_FAMILY,
            mono_size=mono_size,
        ).clamped()

    def _update_preview(self, settings: FontSettings) -> None:
        self.preview_ui.configure(font=(settings.ui_family, settings.ui_size))
        self.preview_mono.configure(font=(settings.mono_family, settings.mono_size))

    def _apply(self) -> None:
        settings = self._collect()
        if settings is None:
            return
        applied = self._on_apply(settings) or settings
        self.ui_family_var.set(applied.ui_family)
        self.ui_size_var.set(applied.ui_size)
        self.mono_family_var.set(applied.mono_family)
        self.mono_size_var.set(applied.mono_size)
        self._update_preview(applied)
        self.status_var.set(
            f"已应用:{applied.ui_family} {applied.ui_size}px / "
            f"{applied.mono_family} {applied.mono_size}px"
        )

    def _reset(self) -> None:
        defaults = FontSettings()
        self.ui_family_var.set(defaults.ui_family)
        self.ui_size_var.set(defaults.ui_size)
        self.mono_family_var.set(defaults.mono_family)
        self.mono_size_var.set(defaults.mono_size)
        self._apply()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        cb, self._on_close_cb = self._on_close_cb, None
        if cb is not None:
            cb()
        self.destroy()
