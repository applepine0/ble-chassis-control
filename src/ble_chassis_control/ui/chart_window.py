"""独立放大的四轮转速曲线窗口。

只负责"大图"视图:数据由 App 推送,配色由 theme.Palette 注入。
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable

from .. import config
from ..chart import WheelChart
from .theme import Palette


class ChartWindow(tk.Toplevel):
    def __init__(
        self,
        master,
        *,
        palette: Palette,
        on_close: Callable[[], None] | None = None,
        maxlen: int = 1200,
        height: int = 520,
    ) -> None:
        super().__init__(master)
        self.palette = palette
        self._on_close_cb = on_close
        self._after_id: str | None = None
        self._closed = False

        self.title("四轮转速曲线 — 放大")
        self.geometry("1200x640")
        self.minsize(800, 480)
        self.configure(bg=palette.bg)

        self.chart = WheelChart(
            self,
            names=config.WHEEL_NAMES,
            unit=config.WHEEL_SPEED_UNIT,
            maxlen=maxlen,
            redraw_ms=config.CHART_REDRAW_MS,
            height=height,
            colors=palette.wheel_colors,
            bg=palette.chart_bg,
            grid=palette.chart_grid,
            axis=palette.chart_axis,
            text=palette.chart_text,
        )
        self.chart.pack(fill="both", expand=True, padx=8, pady=8)

        self.protocol("WM_DELETE_WINDOW", self.close)
        self._tick()

    # ------------------------------------------------------------------
    # 数据
    # ------------------------------------------------------------------
    def push(self, speeds) -> None:
        self.chart.push(speeds)

    def copy_data_from(self, other: WheelChart) -> None:
        self.chart.copy_data_from(other)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def _tick(self) -> None:
        if self._closed:
            return
        try:
            self.chart.maybe_redraw()
            self._after_id = self.after(max(16, int(self.chart.redraw_ms)), self._tick)
        except tk.TclError:
            self._closed = True

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        cb, self._on_close_cb = self._on_close_cb, None
        if cb is not None:
            cb()
        self.destroy()
