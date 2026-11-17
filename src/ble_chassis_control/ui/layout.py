"""UI 布局层:只负责创建控件,不处理业务事件。

由 ``frontend.App._build_ui()`` 调用;控件回调仍指向 App 的控制器方法。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .. import config
from ..chart import WheelChart


def build_main_ui(app) -> None:
    pad = {"padx": 8, "pady": 6}
    app.columnconfigure(0, weight=1)
    app.rowconfigure(1, weight=1)

    # 顶部:链路选择 + 连接栏
    top = ttk.Frame(app, padding=pad["padx"])
    top.grid(row=0, column=0, sticky="ew")
    top.columnconfigure(3, weight=1)

    # 链路类型:串口(HC-05)/ BLE
    app.link_kind = tk.StringVar(value=config.LINK_KIND)
    ttk.Radiobutton(
        top, text="串口 (HC-05)", value="serial",
        variable=app.link_kind, command=app._rebuild_link,
    ).grid(row=0, column=0, sticky="w")
    ttk.Radiobutton(
        top, text="BLE", value="ble",
        variable=app.link_kind, command=app._rebuild_link,
    ).grid(row=0, column=1, sticky="w", padx=(6, 12))

    app.target_label = ttk.Label(top, text="串口:")
    app.target_label.grid(row=0, column=2, sticky="w")
    app.addr_var = tk.StringVar(value="")
    app.addr_var.trace_add("write", app._remember_target)
    ttk.Entry(top, textvariable=app.addr_var, width=22).grid(
        row=0, column=3, sticky="ew", padx=6
    )
    app.refresh_btn = ttk.Button(
        top, text="刷新口", width=8, command=app._refresh_ports
    )
    app.refresh_btn.grid(row=0, column=4, padx=2)
    # 波特率下拉(串口模式下可选;必须与模块/MCU 一致)
    app.baud_var = tk.StringVar(value=str(config.SERIAL_BAUDRATE))
    app.baud_box = ttk.Combobox(
        top, textvariable=app.baud_var, width=8, state="readonly",
        values=[str(b) for b in config.SERIAL_BAUD_CHOICES],
    )
    app.baud_box.grid(row=0, column=5, padx=2)
    app.btn_conn = ttk.Button(top, text="连接", command=app.on_connect)
    app.btn_conn.grid(row=0, column=6, padx=6)
    app.btn_disc = ttk.Button(
        top, text="断开", command=app.on_disconnect, state="disabled"
    )
    app.btn_disc.grid(row=0, column=7, padx=2)
    ttk.Button(top, text="自检", command=app.on_self_check).grid(
        row=0, column=8, padx=2
    )
    app.status_var = tk.StringVar(value="未连接")
    app.status_label = ttk.Label(
        top, textvariable=app.status_var, foreground=app.palette.muted
    )
    app.status_label.grid(row=0, column=9, sticky="e", padx=6)

    # 第二行:串口列表/提示
    app.port_hint = tk.StringVar(value="")
    ttk.Label(top, textvariable=app.port_hint, foreground=app.palette.muted).grid(
        row=1, column=0, columnspan=10, sticky="w", pady=(4, 0)
    )
    app._sync_target_field()

    # 中部:控制 + 日志
    mid = ttk.Frame(app, padding=8)
    mid.grid(row=1, column=0, sticky="nsew")
    mid.columnconfigure(1, weight=1)
    mid.rowconfigure(0, weight=1)

    ctrl = ttk.LabelFrame(mid, text=" 底盘控制 (速度矢量) ", padding=8)
    ctrl.grid(row=0, column=0, sticky="n", padx=(0, 8))
    ctrl.columnconfigure(1, weight=1)

    # 速度大小 m/s
    ttk.Label(ctrl, text="速度 m/s").grid(row=0, column=0, sticky="w")
    app.speed_var = tk.DoubleVar(value=0.0)
    ttk.Scale(
        ctrl,
        from_=0, to=config.VEL_UI_MAX, variable=app.speed_var,
        length=190, command=app._scale_drag,
    ).grid(row=0, column=1, padx=6, pady=4)
    ttk.Spinbox(
        ctrl, from_=0, to=config.VEL_UI_MAX, increment=0.05,
        textvariable=app.speed_var, width=7,
    ).grid(row=0, column=2)

    # 方向角 deg(协议不定义参考系;此处 UI 按车头坐标系、0°=正前、逆时针为正)
    ttk.Label(ctrl, text="方向 deg").grid(row=1, column=0, sticky="w", pady=(6, 0))
    app.deg_var = tk.DoubleVar(value=0.0)
    ttk.Scale(
        ctrl,
        from_=config.DEG_UI_MIN, to=config.DEG_UI_MAX, variable=app.deg_var,
        length=190, command=app._scale_drag,
    ).grid(row=1, column=1, padx=6, pady=4)
    ttk.Spinbox(
        ctrl, from_=config.DEG_UI_MIN, to=config.DEG_UI_MAX, increment=5.0,
        textvariable=app.deg_var, width=7,
    ).grid(row=1, column=2)

    # 自转角速度 rad/s
    ttk.Label(ctrl, text="自转 rad/s").grid(row=2, column=0, sticky="w", pady=(6, 0))
    app.omega_var = tk.DoubleVar(value=0.0)
    ttk.Scale(
        ctrl,
        from_=config.OMEGA_UI_MIN, to=config.OMEGA_UI_MAX, variable=app.omega_var,
        length=190, command=app._scale_drag,
    ).grid(row=2, column=1, padx=6, pady=4)
    ttk.Spinbox(
        ctrl, from_=config.OMEGA_UI_MIN, to=config.OMEGA_UI_MAX, increment=0.1,
        textvariable=app.omega_var, width=7,
    ).grid(row=2, column=2)

    ttk.Label(
        ctrl, text="(UI 约定:车头坐标系,0°=正前,逆时针为正)",
        foreground=app.palette.muted, font=("", 8),
    ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))

    btns = ttk.Frame(ctrl)
    btns.grid(row=4, column=0, columnspan=3, pady=10, sticky="ew")
    app.btn_mot = ttk.Button(btns, text="发送一次", command=app.on_send_vel)
    app.btn_mot.pack(side="left", expand=True, fill="x", padx=(4, 6))
    app.btn_stop = tk.Button(
        btns, text="急停 (Esc)", command=app.on_stop,
        bg=app.palette.err, fg="white",
        activebackground=app.palette.danger_active,
        activeforeground="white", font=("", 10, "bold"),
        width=12, relief="raised", bd=2,
    )
    app.btn_stop.pack(side="right", padx=(0, 4))

    # --- 定频运动下发(θ 按 ω 累积)---
    motion = ttk.LabelFrame(ctrl, text=" 旋转前进(定频下发) ", padding=6)
    motion.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(4, 0))
    motion.columnconfigure(3, weight=1)

    ttk.Label(motion, text="频率 Hz").grid(row=0, column=0, sticky="w")
    app.rate_var = tk.DoubleVar(value=config.MOTION_RATE_HZ)
    ttk.Spinbox(
        motion, from_=config.MOTION_RATE_MIN, to=config.MOTION_RATE_MAX,
        increment=1.0, textvariable=app.rate_var, width=6,
    ).grid(row=0, column=1, padx=(4, 10))
    app.motion_state = tk.StringVar(value="未运行")
    ttk.Label(motion, textvariable=app.motion_state,
              foreground=app.palette.muted).grid(row=0, column=3, sticky="e")

    mbtns = ttk.Frame(motion)
    mbtns.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
    app.btn_motion_start = ttk.Button(
        mbtns, text="启动运动", command=app.on_motion_start
    )
    app.btn_motion_start.pack(side="left", expand=True, fill="x", padx=3)
    app.btn_motion_stop = ttk.Button(
        mbtns, text="停止运动", command=app.on_motion_stop, state="disabled"
    )
    app.btn_motion_stop.pack(side="left", expand=True, fill="x", padx=3)

    ttk.Label(
        motion,
        text="启动后按频率持续下发 θ(t)=θ₀−∫ω·dt(默认反向补偿车体自转)",
        foreground=app.palette.muted, font=("", 8),
    ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 0))

    ttk.Separator(ctrl).grid(row=6, column=0, columnspan=3, sticky="ew", pady=6)

    # 通用 SET 命令(方案 A:物理值 → ×scale 整数)
    ttk.Label(ctrl, text="SET 命令:").grid(row=7, column=0, sticky="w")
    app.set_name = ttk.Combobox(
        ctrl, values=list(config.SET_PARAMS), width=10,
    )
    app.set_name.grid(row=7, column=1, sticky="ew", padx=6)
    app.set_name.set("MAX_SPEED")
    app.set_val = ttk.Entry(ctrl, width=8)
    app.set_val.grid(row=7, column=2)
    app.set_val.insert(0, "0.8")
    app.set_hint_var = tk.StringVar(value="")
    ttk.Label(
        ctrl, textvariable=app.set_hint_var, foreground=app.palette.muted, font=("", 8),
    ).grid(row=8, column=0, columnspan=3, sticky="w")
    app.set_name.bind("<<ComboboxSelected>>", lambda _e: app._update_set_hint())
    app.set_name.bind("<KeyRelease>", lambda _e: app._update_set_hint())
    app.set_val.bind("<KeyRelease>", lambda _e: app._update_set_hint())
    app.btn_set = ttk.Button(ctrl, text="发送 SET", command=app.on_send_set)
    app.btn_set.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(6, 8))

    # 手动透传行
    ttk.Label(ctrl, text="手动发送:").grid(row=10, column=0, sticky="w")
    app.raw_var = tk.StringVar()
    ttk.Entry(ctrl, textvariable=app.raw_var).grid(
        row=10, column=1, columnspan=2, sticky="ew", padx=6
    )
    app.btn_raw = ttk.Button(ctrl, text="发送", command=app.on_send_raw)
    app.btn_raw.grid(row=11, column=0, columnspan=3, sticky="ew", pady=8)
    app._update_set_hint()

    # 右侧:日志 + 转速曲线
    right = ttk.Frame(mid)
    right.grid(row=0, column=1, sticky="nsew")
    right.columnconfigure(0, weight=1)
    right.rowconfigure(0, weight=1)     # 日志占主要空间
    right.rowconfigure(1, weight=0)     # 曲线固定高度

    logf = ttk.LabelFrame(right, text=" 通信日志 ", padding=4)
    logf.grid(row=0, column=0, sticky="nsew")
    logf.columnconfigure(0, weight=1)
    logf.rowconfigure(0, weight=1)

    app.log = tk.Text(
        logf, state="disabled", wrap="word",
        font=("Consolas", 10), bg=app.palette.log_bg,
        fg=app.palette.text, insertbackground=app.palette.text,
        relief="flat", bd=0,
    )
    app.log.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(logf, command=app.log.yview)
    sb.grid(row=0, column=1, sticky="ns")
    app.log.configure(yscrollcommand=sb.set)
    for tag, color in (
        ("sys", app.palette.log_sys),
        ("tx", app.palette.log_tx),
        ("rx", app.palette.log_rx),
        ("err", app.palette.log_err),
    ):
        app.log.tag_configure(tag, foreground=color)

    app.autoscroll_var = tk.BooleanVar(value=True)
    logbar = ttk.Frame(logf)
    logbar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=4)
    ttk.Checkbutton(
        logbar, text="自动滚动", variable=app.autoscroll_var
    ).pack(side="left")
    ttk.Button(
        logbar, text="清空日志", width=10, command=app.clear_log
    ).pack(side="right")

    # 四轮转速实时曲线
    chartf = ttk.LabelFrame(right, text=" 四轮转速曲线 ", padding=4)
    chartf.grid(row=1, column=0, sticky="ew", pady=(6, 0))
    chartf.columnconfigure(0, weight=1)
    app.chart = WheelChart(
        chartf,
        names=config.WHEEL_NAMES,
        unit=config.WHEEL_SPEED_UNIT,
        maxlen=config.CHART_MAXLEN,
        redraw_ms=config.CHART_REDRAW_MS,
        height=config.CHART_HEIGHT,
        colors=app.palette.wheel_colors,
        bg=app.palette.chart_bg,
        grid=app.palette.chart_grid,
        axis=app.palette.chart_axis,
        text=app.palette.chart_text,
        on_expand=app.on_expand_chart,
    )
    app.chart.grid(row=0, column=0, sticky="ew")

    # 底部:四轮速度反馈 + 链路健康度
    bottom = ttk.Frame(app, padding=(8, 0))
    bottom.grid(row=2, column=0, sticky="ew")
    bottom.columnconfigure(0, weight=1)

    app.wheel_var = tk.StringVar(value="四轮速度: (无反馈)")
    ttk.Label(
        bottom, textvariable=app.wheel_var,
        font=("Consolas", 10),
    ).grid(row=0, column=0, sticky="w", pady=2)

    app.link_health_var = tk.StringVar(value="")
    ttk.Label(bottom, textvariable=app.link_health_var, foreground=app.palette.muted).grid(
        row=1, column=0, sticky="w"
    )

    # 链路详情(GATT 树 / 串口参数)
    app.info_var = tk.StringVar(value="(未连接)")
    ttk.Label(app, textvariable=app.info_var, foreground=app.palette.muted).grid(
        row=3, column=0, sticky="w", padx=8, pady=4
    )
