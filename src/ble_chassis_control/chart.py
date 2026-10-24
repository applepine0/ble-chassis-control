"""四轮转速实时曲线图(纯 tkinter Canvas 实现,无第三方依赖)。

设计要点
--------
* **环形缓冲**:固定容量(``maxlen``),高频数据不无限增长内存;
* **自动量程**:纵轴按当前数据范围自动缩放,并留 10% 余量;
  零线始终可见(轮速有正负,过零必须能看出来);
* **四条曲线**:右前/左前/左后/右后,颜色区分 + 图例;
* **节流重绘**:数据可能 100Hz 到达,但重绘按 ``redraw_ms`` 限制(默认 50ms
  → 20fps),避免 Canvas 成为 CPU 瓶颈;
* **冻结检测**:若一段时间无新数据(如断链),曲线保持最后状态,
  状态行提示"(无数据)"。
"""
from __future__ import annotations

import time
import tkinter as tk
from collections import deque
from tkinter import ttk

# 四轮配色(与轮序 w0~w3 对应)
WHEEL_COLORS = ("#D32F2F", "#1976D2", "#388E3C", "#F57C00")  # 右前 左前 左后 右后

C_BG = "#1E1E1E"
C_GRID = "#3A3A3A"
C_AXIS = "#6A6A6A"
C_TEXT = "#CCCCCC"


class WheelChart(ttk.Frame):
    """四轮转速曲线控件。

    用法::

        chart = WheelChart(parent, names=("右前","左前","左后","右后"), unit="rpm")
        chart.grid(...)
        chart.push((120.0, -250.0, 0.0, 385.0))   # 每收到一帧调用一次
        chart.clear()
    """

    def __init__(
        self,
        master,
        *,
        names: tuple[str, ...] = ("右前", "左前", "左后", "右后"),
        unit: str = "rpm",
        maxlen: int = 600,          # 保留最近 N 个采样(600 @100Hz = 6 秒)
        redraw_ms: int = 50,        # 重绘节流
        height: int = 150,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        self.names = names
        self.unit = unit
        self.maxlen = maxlen
        self.redraw_ms = redraw_ms

        self._series: list[deque[float]] = [
            deque(maxlen=maxlen) for _ in names
        ]
        self._last_push = 0.0
        self._dirty = False
        self._last_draw = 0.0
        self._auto_scale = tk.BooleanVar(value=True)
        self._frozen_var = tk.StringVar(value="(无数据)")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # 顶部:图例 + 控制
        top = ttk.Frame(self)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        for i, (name, color) in enumerate(zip(names, WHEEL_COLORS)):
            # 注意:ttk.Frame 不支持 cget("background")(styled widget),
            # 故图例色块用 tk.Label,背景由父容器继承(不显式设置)。
            tk.Label(top, text="■", fg=color, font=("", 11)).grid(
                row=0, column=i, padx=(0, 1)
            )
            ttk.Label(top, text=name).grid(row=0, column=i + 10, padx=(0, 8))
        ttk.Checkbutton(
            top, text="自动量程", variable=self._auto_scale,
            command=self._on_toggle_scale,
        ).grid(row=0, column=30, padx=(6, 6))
        ttk.Button(top, text="清空", width=6, command=self.clear).grid(
            row=0, column=31
        )
        ttk.Label(top, textvariable=self._frozen_var, foreground=C_AXIS).grid(
            row=0, column=32, padx=(8, 0)
        )

        # 画布
        self.canvas = tk.Canvas(
            self, height=height, bg=C_BG, highlightthickness=0
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _e: self._draw(force=True))

    # ------------------------------------------------------------------
    # 数据接口
    # ------------------------------------------------------------------
    def push(self, speeds) -> None:
        """推入一帧四轮转速(任意长度序列,按 w0..w3 顺序)。"""
        for i, dq in enumerate(self._series):
            if i < len(speeds):
                dq.append(float(speeds[i]))
        self._last_push = time.monotonic()
        self._dirty = True

    def clear(self) -> None:
        for dq in self._series:
            dq.clear()
        self._dirty = True
        self._frozen_var.set("(无数据)")
        self._draw(force=True)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _on_toggle_scale(self) -> None:
        self._draw(force=True)

    def maybe_redraw(self) -> None:
        """由主循环定期调用;内部按 redraw_ms 节流。"""
        now = time.monotonic()
        if self._dirty and (now - self._last_draw) * 1000 >= self.redraw_ms:
            self._draw()
        # 无数据提示(2 秒无新帧)
        if self._last_push and now - self._last_push > 2.0:
            self._frozen_var.set("(无数据)")
        elif self._last_push:
            self._frozen_var.set("")

    def _draw(self, force: bool = False) -> None:
        c = self.canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 1 or h <= 1:               # 尚未布局
            return
        self._dirty = False
        self._last_draw = time.monotonic()
        c.delete("all")

        # 计算量程
        all_vals = [v for dq in self._series for v in dq]
        if self._auto_scale.get() and all_vals:
            vmax = max(all_vals)
            vmin = min(all_vals)
            span = max(vmax - vmin, 1.0)
            pad = span * 0.1
            hi = vmax + pad
            lo = vmin - pad
            # 保证零线可见(轮速有正负)
            if lo > 0:
                lo = 0.0
            if hi < 0:
                hi = 0.0
        else:
            hi, lo = 500.0, -500.0

        if hi == lo:
            hi, lo = hi + 1.0, lo - 1.0

        def to_y(v: float) -> float:
            return h - (v - lo) / (hi - lo) * h

        # 网格(4 等分)+ 刻度
        for k in range(5):
            y = h * k / 4
            v = hi - (hi - lo) * k / 4
            c.create_line(0, y, w, y, fill=C_GRID)
            c.create_text(4, y + 6, anchor="w", fill=C_TEXT,
                          text=f"{v:+.0f}", font=("Consolas", 7))

        # 零线加粗
        if lo <= 0 <= hi:
            y0 = to_y(0.0)
            c.create_line(0, y0, w, y0, fill=C_AXIS, dash=(4, 3))

        # 曲线
        steps = w - 1
        for idx, dq in enumerate(self._series):
            n = len(dq)
            if n < 2:
                continue
            pts = []
            for k in range(min(steps, n - 1) + 1):
                # 取最近 (steps+1) 个点,保证最新数据贴右边缘
                pos = n - (min(steps, n - 1) + 1) + k
                if 0 <= pos < n:
                    x = k * w / min(steps, n - 1) if min(steps, n - 1) else 0
                    pts.extend((x, to_y(dq[pos])))
            if len(pts) >= 4:
                c.create_line(*pts, fill=WHEEL_COLORS[idx % len(WHEEL_COLORS)],
                              width=1, smooth=False)

        # 单位标注
        c.create_text(w - 4, 8, anchor="ne", fill=C_TEXT, text=self.unit,
                      font=("Consolas", 8))
