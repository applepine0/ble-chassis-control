"""辅助执行器控制面板(升降 / 辅助轮 / 气缸)。

三个模块彼此独立:
  * 某个模块的 ``config.EXT_MODULES[key]`` 为 False 时,只禁用该行;
  * 每个模块各自下发自己的协议行,不共享状态机。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import config
from .theme import FONT_SMALL


class ExtensionsPanel(ttk.LabelFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, text=" 辅助执行器 ", padding=6)
        self.app = app
        self.columnconfigure(1, weight=1)

        self._build_lift()
        self._build_aux()
        self._build_cyl()
        self._build_feedback()

    @staticmethod
    def _enabled(key: str) -> bool:
        return bool(config.EXT_MODULES.get(key, False))

    def _state(self, key: str) -> str:
        return "normal" if self._enabled(key) else "disabled"

    def _label(self, key: str, text: str) -> str:
        return text if self._enabled(key) else f"{text}(未启用)"

    # ------------------------------------------------------------------
    # 升降
    # ------------------------------------------------------------------
    def _build_lift(self) -> None:
        ttk.Label(self, text=self._label("lift", "升降 mm")).grid(
            row=0, column=0, sticky="w", pady=2
        )
        self.app.lift_var = tk.IntVar(value=0)
        spin = ttk.Spinbox(
            self,
            from_=config.LIFT_MIN_MM,
            to=config.LIFT_MAX_MM,
            increment=1,
            textvariable=self.app.lift_var,
            width=8,
            state=self._state("lift"),
        )
        spin.grid(row=0, column=1, sticky="w", padx=6, pady=2)
        self.app.btn_lift = ttk.Button(
            self,
            text="下发",
            width=6,
            command=self.app.on_send_lift,
            state=self._state("lift"),
        )
        self.app.btn_lift.grid(row=0, column=2, padx=(4, 0))

    # ------------------------------------------------------------------
    # 辅助轮
    # ------------------------------------------------------------------
    def _build_aux(self) -> None:
        ttk.Label(self, text=self._label("aux", "辅助轮1 °")).grid(
            row=1, column=0, sticky="w", pady=2
        )
        self.app.aux_var = tk.DoubleVar(value=0.0)
        spin = ttk.Spinbox(
            self,
            from_=config.AUX_MIN_D10 / 10,
            to=config.AUX_MAX_D10 / 10,
            increment=0.1,
            format="%.1f",
            textvariable=self.app.aux_var,
            width=8,
            state=self._state("aux"),
        )
        spin.grid(row=1, column=1, sticky="w", padx=6, pady=2)
        self.app.btn_aux = ttk.Button(
            self,
            text="下发",
            width=6,
            command=self.app.on_send_aux,
            state=self._state("aux"),
        )
        self.app.btn_aux.grid(row=1, column=2, padx=(4, 0))

    # ------------------------------------------------------------------
    # 气缸
    # ------------------------------------------------------------------
    def _build_cyl(self) -> None:
        ttk.Label(self, text=self._label("cyl", "气缸1")).grid(row=2, column=0, sticky="w", pady=2)
        self.app.cyl_var = tk.StringVar(value="缩回")
        combo = ttk.Combobox(
            self,
            textvariable=self.app.cyl_var,
            values=("缩回", "伸出"),
            state="readonly" if self._enabled("cyl") else "disabled",
            width=8,
        )
        combo.grid(row=2, column=1, sticky="w", padx=6, pady=2)
        self.app.btn_cyl = ttk.Button(
            self,
            text="下发",
            width=6,
            command=self.app.on_send_cyl,
            state=self._state("cyl"),
        )
        self.app.btn_cyl.grid(row=2, column=2, padx=(4, 0))

    # ------------------------------------------------------------------
    # 反馈
    # ------------------------------------------------------------------
    def _build_feedback(self) -> None:
        self.app.ext_status_var = tk.StringVar(value="反馈: (无)")
        ttk.Label(
            self,
            textvariable=self.app.ext_status_var,
            style="Muted.TLabel",
            font=FONT_SMALL,
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(
            self,
            text="STOP 仅停底盘;辅助执行器保持当前目标",
            style="Muted.TLabel",
            font=FONT_SMALL,
        ).grid(row=4, column=0, columnspan=3, sticky="w")
