"""上位机控制器:连接管理、底盘控制、收发日志与状态机。

界面构建在 ``ui/layout.py``,主题/DPI/配色在 ``ui/theme.py``;
本模块不直接创建控件(除 TK 变量/快捷键外),便于替换视图。

线程模型:
  * asyncio 事件循环运行在后台守护线程,tk 主循环在 GUI 线程;
  * 界面提交异步任务用 ``asyncio.run_coroutine_threadsafe``;
  * 后端回调(接收/状态)经 ``queue.Queue`` 传回 GUI 线程,由 ``after`` 轮询。
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING

from . import config
from .backend import BLEBackend, BLEError
from .backend_serial import SerialBackend, available_ports
from .link import ChassisLink
from .motion import MotionRunner
from .protocol import ParsedMessage, Protocol
from .ui.theme import Palette, apply_theme, enable_dpi_awareness

if TYPE_CHECKING:
    from concurrent.futures import Future as ConcurrentFuture

    from .chart import WheelChart
    from .protocol import LinkStats
    from .ui.chart_window import ChartWindow

MAX_LOG_LINES = 5000


def now() -> str:
    return time.strftime("%H:%M:%S")


class App(tk.Tk):
    # 以下控件由 ui/layout.py 构建并注入,显式声明供 Pylance 使用
    palette: Palette
    link_kind: tk.StringVar
    target_label: ttk.Label
    addr_var: tk.StringVar
    baud_var: tk.StringVar
    baud_box: ttk.Combobox
    refresh_btn: ttk.Button
    btn_conn: ttk.Button
    btn_disc: ttk.Button
    status_var: tk.StringVar
    status_label: ttk.Label
    port_hint: tk.StringVar

    speed_var: tk.DoubleVar
    deg_var: tk.DoubleVar
    omega_var: tk.DoubleVar
    btn_mot: ttk.Button
    btn_stop: tk.Button

    rate_var: tk.DoubleVar
    motion_state: tk.StringVar
    btn_motion_start: ttk.Button
    btn_motion_stop: ttk.Button

    set_name: ttk.Combobox
    set_val: ttk.Entry
    set_hint_var: tk.StringVar
    btn_set: ttk.Button
    raw_var: tk.StringVar
    btn_raw: ttk.Button

    log: tk.Text
    autoscroll_var: tk.BooleanVar
    chart: WheelChart

    wheel_var: tk.StringVar
    link_health_var: tk.StringVar
    info_var: tk.StringVar

    chart_window: ChartWindow | None
    _last_stats: LinkStats | None
    _single_send_hint_shown: bool

    def __init__(self) -> None:
        enable_dpi_awareness()
        super().__init__()
        self.palette = apply_theme(self, config.UI_THEME)
        self.title("底盘上位机 — HC-05 串口 / BLE 透传控制台")
        self.geometry("900x640")
        self.minsize(780, 540)

        # 异步基础设施
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(
            target=self._loop_entry, name="asyncio-loop", daemon=True
        )
        self.loop_thread.start()

        # 当前链路(默认串口;由界面单选切换)
        self.backend: ChassisLink = self._make_link(config.LINK_KIND)
        self.motion: MotionRunner | None = None  # 定频运动下发器
        self.rx_queue: queue.Queue[tuple] = queue.Queue()

        # 界面状态
        self._reconnecting = False  # 是否处于自动重连流程
        self._last_serial_port = config.SERIAL_PORT
        self._frame_times: deque[float] = deque(maxlen=100)
        self._wheel_hz = 0.0
        self._last_stats = None
        self.chart_window = None  # 独立放大曲线窗口

        self._build_ui()
        self.bind("<Escape>", self._on_escape)
        self.after(50, self._poll_queues)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    # 链路构建
    # ------------------------------------------------------------------
    def _make_link(self, kind: str) -> ChassisLink:
        """按类型创建链路对象(BLE / 串口),并挂上四轮速度帧回调。"""
        if kind == "ble":
            return BLEBackend(
                on_line=self._on_line_cb,
                on_state=self._on_state_cb,
                on_wheel=self._on_wheel_cb,
                on_stats=self._on_stats_cb,
            )
        return SerialBackend(
            on_line=self._on_line_cb,
            on_state=self._on_state_cb,
            on_wheel=self._on_wheel_cb,
            on_stats=self._on_stats_cb,
        )

    # ------------------------------------------------------------------
    # 四轮速度反馈(二进制帧)回调 —— 后台线程,经队列送 GUI
    # ------------------------------------------------------------------
    def _on_wheel_cb(self, frame) -> None:
        self.rx_queue.put(("wheel", frame))

    def _on_stats_cb(self, stats) -> None:
        self.rx_queue.put(("stats", stats))

    def _show_wheel_frame(self, frame) -> None:
        """显示四轮速度(定长帧,高频刷新,不写日志避免刷屏)。"""
        unit = frame.unit or ""
        vals = "  ".join(f"{name}={v:+6.0f}" for name, v in frame.labeled())
        self.wheel_var.set(f"四轮转速 [{unit}]: {vals}   (seq={frame.seq})")
        # 记录到达时间,用于计算实测反馈帧率
        self._frame_times.append(time.monotonic())
        if len(self._frame_times) >= 2:
            span = self._frame_times[-1] - self._frame_times[0]
            self._wheel_hz = (len(self._frame_times) - 1) / span if span > 0 else 0.0
        # 推入曲线(高频调用,内部有节流重绘)
        self.chart.push(frame.speeds)
        if self.chart_window is not None:
            self.chart_window.push(frame.speeds)

    def on_expand_chart(self) -> None:
        """打开/聚焦独立放大的四轮转速曲线窗口。"""
        if self.chart_window is not None:
            self.chart_window.lift()
            self.chart_window.focus_force()
            return
        from .ui.chart_window import ChartWindow

        self.chart_window = win = ChartWindow(
            self,
            palette=self.palette,
            on_close=self._on_chart_window_closed,
            maxlen=max(config.CHART_MAXLEN, 1200),
            height=520,
        )
        win.copy_data_from(self.chart)
        self.log_sys("曲线已放大为独立窗口")

    def _on_chart_window_closed(self) -> None:
        self.chart_window = None

    def _show_link_stats(self, stats) -> None:
        """链路健康度:丢帧率 / CRC 错误 / 杂散 / 实测帧率。"""
        self._last_stats = stats
        now = time.monotonic()
        hz = self._wheel_hz
        if not self._frame_times or now - self._frame_times[-1] > 2.0:
            hz = 0.0
        err = (getattr(self.backend, "last_error", "") or "").strip()
        if len(err) > 80:
            err = err[:80] + "…"
        suffix = f"  最近错误: {err}" if err else ""
        self.link_health_var.set(
            f"反馈链路: 收帧 {stats.frames_ok}  丢帧 {stats.frames_lost} "
            f"({stats.loss_rate:.1%})  CRC错 {stats.crc_errors}  "
            f"杂散 {stats.garbage_bytes}B  实测 ≈{hz:.0f} Hz{suffix}"
        )

    # ------------------------------------------------------------------
    # 异步循环
    # ------------------------------------------------------------------
    def _loop_entry(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _submit(self, coro, *, on_success=None, on_error=None) -> None:
        """把协程提交到后台事件循环;结果/异常经线程安全队列送回 GUI 线程。"""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def done(f: ConcurrentFuture) -> None:  # 在事件循环线程执行
            try:
                result = f.result()
                if on_success is not None:
                    cb = on_success
                    self.rx_queue.put(("ui", lambda r=result: cb(r)))
            except BLEError as exc:
                # 注意:必须在 lambda 创建时绑定值 —— except 变量在块结束时会被
                # Python 3 销毁,延迟引用会抛 NameError(实测踩过)
                err = str(exc)
                if on_error is not None:
                    cb = on_error
                    self.rx_queue.put(("ui", lambda e=err: cb(e)))
                else:
                    self.rx_queue.put(("err", err))
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                if on_error is not None:
                    cb = on_error
                    self.rx_queue.put(("ui", lambda e=err: cb(e)))
                else:
                    self.rx_queue.put(("err", err))

        fut.add_done_callback(done)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """构建界面:布局在 ui/layout.py,本类只做控制器。"""
        from .ui.layout import build_main_ui

        build_main_ui(self)

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _remember_target(self, *_args) -> None:
        """记住用户/自动填入的串口,切到 BLE 再切回时恢复。"""
        if self.link_kind.get() == "serial":
            value = self.addr_var.get().strip()
            if value:
                self._last_serial_port = value

    def _sync_target_field(self) -> None:
        """切换链路时,同步目标字段与串口相关控件。"""
        if self.link_kind.get() == "ble":
            self.target_label.configure(text="模块地址:")
            self.addr_var.set(config.DEVICE_ADDRESS)
            self.baud_box.grid_remove()
            self.refresh_btn.grid_remove()
            self.port_hint.set("BLE 模式:填模块地址后点[自检]扫描")
        else:
            self.target_label.configure(text="串口号:")
            self.addr_var.set(self._last_serial_port)
            self.baud_box.grid()
            self.refresh_btn.grid()
            self._refresh_ports()

    def _refresh_ports(self) -> None:
        """扫描可用串口并提示。"""
        if self.link_kind.get() != "serial":
            self.port_hint.set("BLE 模式:填模块地址后点[自检]扫描")
            return
        try:
            ports = available_ports()
        except Exception as exc:
            self.port_hint.set(f"串口枚举失败:{exc}")
            return
        if not ports:
            self.port_hint.set(
                "未检测到串口:请确认 USB-TTL 已插好,或 HC-05 已在 Windows 蓝牙中配对"
            )
            return
        listing = " | ".join(f"{d}: {s}" for d, s in ports)
        self.port_hint.set(f"可用串口:{listing}")

        # 自动选“带真实地址”的蓝牙口;旧的地址空端口不选
        live_bt = [d for d, s in ports if "[蓝牙 " in s]
        stale_bt = [d for d, s in ports if "地址空" in s]
        current = self.addr_var.get().strip()
        if current in stale_bt and len(live_bt) == 1:
            self.addr_var.set(live_bt[0])
            self.port_hint.set(f"可用串口:{listing} | 已将旧口 {current} 切换为 {live_bt[0]}")
        elif not current:
            if len(live_bt) == 1:
                self.addr_var.set(live_bt[0])
            elif len(ports) == 1:
                self.addr_var.set(ports[0][0])

        self._last_serial_port = self.addr_var.get().strip()

    def _scale_drag(self, _=None) -> None:
        # 滑块拖动时不下发,避免刷屏;仅同步显示
        pass

    def _update_set_hint(self) -> None:
        """按参数名/当前输入显示单位、缩放与线上整数预览。"""
        name = self.set_name.get().strip()
        if not name:
            self.set_hint_var.set("请输入参数名(未登记参数按 ×1 发送)")
            return
        scale, unit, desc = config.set_param(name)
        try:
            value = float(self.set_val.get())
        except ValueError:
            value = None
        if value is None:
            suffix = f"单位 {unit}," if unit else ""
            self.set_hint_var.set(f"{desc}: {suffix}缩放 ×{scale}")
            return
        raw = int(round(value * scale))
        shown = f"{value:g}" + (f" {unit}" if unit else "")
        self.set_hint_var.set(f"{desc}: {shown} ×{scale} → 线上 {raw}")

    def on_connect(self) -> None:
        # 防护:已连接 / 连接中时忽略重复点击,避免二次打开串口自锁
        if self.backend.is_connected:
            self.log_sys('已连接,忽略重复的"连接"点击')
            return
        self._reconnecting = False
        value = self.addr_var.get().strip()
        if self.link_kind.get() == "ble":
            self.backend.address = value.upper()  # type: ignore[attr-defined]
        else:
            self.backend.port = value  # type: ignore[attr-defined]
            self._last_serial_port = value
            try:
                self.backend.baudrate = int(self.baud_var.get())  # type: ignore[attr-defined]
            except (tk.TclError, ValueError):
                messagebox.showwarning("输入错误", "波特率必须是整数")
                return
        self.log_sys(f"连接目标:{self.backend.kind} / {self.backend.target or '(自动)'}")
        self.btn_conn.configure(state="disabled")
        self.btn_disc.configure(state="disabled")
        self._set_status("连接中 ...", self.palette.warn)
        self._submit(
            self.backend.connect(),
            # 注意:成功回调只调 _set_connected —— 不能带 _ready_buttons,
            # 那会把连接按钮错误地恢复为可点(实测 bug)
            on_success=lambda _r: self._set_connected(),
            on_error=lambda err: (
                self._set_status("连接失败", self.palette.err),
                self.log_err(err),
                self._ready_buttons(),
            ),
        )

    def on_disconnect(self) -> None:
        self._reconnecting = False
        if self.motion is not None and self.motion.is_running:
            self.motion.stop()
            self.log_sys("断开前:已终止运动下发,等待 STOP 下发 ...")
            self.btn_disc.configure(state="disabled")
            self._set_status("断开中 ...", self.palette.warn)
            self._submit(
                self._disconnect_after_motion(),
                on_success=lambda _r: self._set_disconnected(),
                on_error=lambda err: (self.log_err(err), self._set_disconnected()),
            )
            return
        self._submit(self.backend.disconnect())
        self._set_disconnected()

    async def _disconnect_after_motion(self) -> None:
        """先等运动协程退出(其内部会发 STOP),再关闭链路。"""
        motion = self.motion
        if motion is not None:
            for _ in range(30):  # 最多等 3 秒
                if not motion.is_running:
                    break
                await asyncio.sleep(0.1)
        await self.backend.disconnect()

    def _rebuild_link(self) -> None:
        """切换链路类型:先停运动、等 STOP 发出,再断开旧链路并创建新的。"""
        old = self.backend
        if self.motion is not None and self.motion.is_running:
            self.motion.stop()
            self.log_sys("切换链路前:已终止运动下发,等待 STOP 下发 ...")
            self._submit(self._disconnect_old_after_motion(old))
        elif old.is_connected:
            self._submit(old.disconnect())
        self.backend = self._make_link(self.link_kind.get())
        self.log_sys(f"链路已切换为:{'BLE' if self.link_kind.get() == 'ble' else '串口'}")
        self._sync_target_field()

    async def _disconnect_old_after_motion(self, old: ChassisLink) -> None:
        """切换链路时:等旧链路上的运动协程发出 STOP,再断开旧链路。"""
        motion = self.motion
        if motion is not None:
            for _ in range(30):  # 最多等 3 秒
                if not motion.is_running:
                    break
                await asyncio.sleep(0.1)
        await old.disconnect()

    def on_self_check(self) -> None:
        """一键自检:串口列出可用口;BLE 扫描目标模块广播。"""
        if self.link_kind.get() == "serial":
            self._refresh_ports()
            self.log_sys("串口自检完成(见上方可用串口列表)")
            return
        address = self.addr_var.get().strip().upper()
        self.backend.address = address  # type: ignore[attr-defined]
        self.log_sys(f"自检:扫描 {address} (8s)...")
        self._set_status("自检中 ...", self.palette.warn)
        self._submit(
            self._do_self_check(),
            on_success=lambda _r: self._set_status("未连接", self.palette.muted),
            on_error=lambda err: (
                self._set_status("自检失败", self.palette.err),
                self.log_err(err),
            ),
        )

    async def _do_self_check(self) -> None:
        from bleak import BleakScanner

        target = getattr(self.backend, "address", "")
        devices = await BleakScanner.discover(timeout=8.0, return_adv=True)
        hits = 0
        for addr, (dev, adv) in devices.items():
            name = (dev.name or "").upper()
            if addr.upper() == target or name == "JDY-23":
                hits += 1
                uuids = ", ".join(adv.service_uuids) or "(无服务广播)"
                self.log_sys(f"发现 {addr}  name={dev.name!r} RSSI={adv.rssi} service=[{uuids}]")
        if hits == 0:
            self._set_status("未发现目标模块", self.palette.err)
            self.log_err(
                f"未发现 {target}(或名 JDY-23):请确认模块已上电并在广播;"
                "若在广播仍连不上,按 README 排查清单检查占用/休眠/iBeacon"
            )
        else:
            self.log_sys("自检完成:模块在广播;如仍连接超时,请执行 README 排查清单")

    def on_send_vel(self) -> None:
        """发送速度矢量:速度大小 m/s + 方向 deg + 自转 rad/s。"""
        try:
            speed = float(self.speed_var.get())
            deg = float(self.deg_var.get())
            omega = float(self.omega_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("输入错误", "速度/方向/自转 必须是数字")
            return
        self.log_tx(f"VEL {speed:g} m/s, {deg:g}°, {omega:g} rad/s")
        self._submit(self.backend.send_vel(speed, deg, omega))
        if not getattr(self, "_single_send_hint_shown", False):
            self._single_send_hint_shown = True
            self.log_sys(
                "提示:VEL 是速度设定值,底盘会保持执行直到收到新指令或 STOP;"
                "若 MCU 启用链路看门狗,超时未刷新才会停车。"
                "需要 θ 随 ω 累积(旋转前进)请用[启动运动]"
            )

    def _on_escape(self, _event=None) -> str:
        """Esc 急停(窗口内任意焦点均可触发)。"""
        self.on_stop()
        return "break"

    def on_stop(self) -> None:
        """急停:先终止定频运动(若在运行),再发 STOP。"""
        if self.motion is not None and self.motion.is_running:
            self.motion.stop()
            self.log_sys("急停:已终止运动下发")
        self.speed_var.set(0.0)
        self.omega_var.set(0.0)
        if not self.backend.is_connected:
            self.log_sys("急停:链路未连接,无法下发 STOP")
            return
        self.log_tx("STOP 急停")
        self._submit(self.backend.send_stop())

    # ------------------------------------------------------------------
    # 定频运动下发(θ 按 ω 累积 —— 旋转前进)
    # ------------------------------------------------------------------
    def on_motion_start(self) -> None:
        """启动定频下发:θ(t) = θ₀ + ∫ω·dt,持续下发直到停止。"""
        if self.motion is not None and self.motion.is_running:
            self.log_sys("运动已在运行,忽略重复启动")
            return
        if not self.backend.is_connected:
            messagebox.showwarning("未连接", "请先连接链路,再启动运动")
            return
        try:
            v = float(self.speed_var.get())
            theta0 = float(self.deg_var.get())
            omega = float(self.omega_var.get())
            rate = float(self.rate_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("输入错误", "速度/方向/自转/频率 必须是数字")
            return
        if rate <= 0:
            messagebox.showwarning("输入错误", "频率必须大于 0")
            return

        self.motion = motion = MotionRunner(
            self.backend,
            v=v,
            theta0=theta0,
            omega=omega,
            rate_hz=rate,
            on_tick=self._on_motion_tick,
            stop_on_error=config.MOTION_STOP_ON_ERROR,
        )
        self.btn_motion_start.configure(state="disabled")
        self.btn_motion_stop.configure(state="normal")
        self.motion_state.set(f"运行中 @{rate:g}Hz")
        self.log_sys(
            f"启动运动:v={v:g} m/s, θ₀={theta0:g}°, ω={omega:g} rad/s, {rate:g}Hz(θ 将按 ω 累积)"
        )
        self._submit(
            motion.run(),
            on_success=lambda elapsed: self._on_motion_finished(elapsed),
            on_error=lambda err: (
                self.log_err(f"运动异常:{err}"),
                self._reset_motion_buttons("异常停止"),
            ),
        )

    def on_motion_stop(self) -> None:
        if self.motion is None or not self.motion.is_running:
            return
        self.log_sys("请求停止运动(将下发 STOP)")
        self.motion.stop()
        self.motion_state.set("停止中 ...")

    def _on_motion_tick(self, tick) -> None:
        """每次下发后的回调(在事件循环线程)—— 只更新界面状态,避免刷屏。"""
        self.rx_queue.put(("ui", lambda t=tick: self._update_motion_status(t)))

    def _update_motion_status(self, tick) -> None:
        self.motion_state.set(
            f"运行中 θ={tick.theta_deg:7.1f}° "
            f"v={tick.v_mps:g} ω={tick.omega_radps:g} "
            f"#{tick.index} t={tick.elapsed:.1f}s"
        )

    def _on_motion_finished(self, elapsed: float) -> None:
        err = self.motion.last_error if self.motion is not None else ""
        if err:
            self.log_err(f"运动异常停止:{err}")
            self._reset_motion_buttons("异常停止")
            return
        self.log_sys(f"运动结束(运行 {elapsed:.1f}s),已下发 STOP")
        self._reset_motion_buttons("已停止")

    def _reset_motion_buttons(self, state: str) -> None:
        self.btn_motion_start.configure(state="normal")
        self.btn_motion_stop.configure(state="disabled")
        self.motion_state.set(state)

    def on_send_set(self) -> None:
        name = self.set_name.get().strip()
        if not name:
            messagebox.showwarning("输入错误", "SET 参数名不能为空")
            return
        try:
            value = float(self.set_val.get())
        except ValueError:
            messagebox.showwarning("输入错误", "SET 数值必须是数字")
            return
        scale, unit, _ = config.set_param(name)
        cmd = Protocol.encode_set(name, value, scale=scale).decode("utf-8").strip()
        shown = f"{value:g}" + (f" {unit}" if unit else "")
        self.log_tx(f"SET {name} = {shown} → {cmd} (×{scale})")
        self._submit(self.backend.send_set(name, value, scale=scale))

    def on_send_raw(self) -> None:
        text = self.raw_var.get()
        if not text.strip():
            return
        self.log_tx(text)
        self._submit(self.backend.send_text(text))

    # ------------------------------------------------------------------
    # 后端回调 -> 队列
    # ------------------------------------------------------------------
    def _on_line_cb(self, line: str, parsed: ParsedMessage) -> None:
        self.rx_queue.put(("line", (line, parsed)))

    def _on_state_cb(self, text: str) -> None:
        self.rx_queue.put(("state", text))

    def _handle_state(self, text: str) -> None:
        """把后端状态文本同步为状态栏/按钮状态。"""
        self.log_sys(text)
        if (
            "自动重连成功" in text
            or "串口已打开" in text
            or ("连接成功" in text and "失败" not in text)
        ):
            self._set_connected()
        elif "连接中断" in text or "意外断开" in text:
            self._reconnecting = True
            self._set_status("链路中断,等待重连 ...", self.palette.err)
            self.btn_conn.configure(state="disabled")
            self.btn_disc.configure(state="normal")
        elif "自动重连" in text:
            self._reconnecting = True
            if "失败" in text:
                self._set_status("自动重连失败,继续重试 ...", self.palette.err)
            else:
                self._set_status("自动重连中 ...", self.palette.warn)
            self.btn_conn.configure(state="disabled")
            self.btn_disc.configure(state="normal")
        elif "已断开" in text or "串口已关闭" in text:
            if not self._reconnecting:
                self._set_disconnected()
        elif "打开串口第" in text or "连接第" in text:
            self._set_status(
                "自动重连中 ..." if self._reconnecting else "连接中 ...", self.palette.warn
            )

    def _poll_queues(self) -> None:
        try:
            while True:
                kind, payload = self.rx_queue.get_nowait()
                if kind == "line":
                    line, parsed = payload
                    if parsed.kind == "tel":
                        kv = ", ".join(f"{k}={v:g}" for k, v in parsed.params["values"].items())
                        self.log_rx(f"TEL {kv}  |  {line}")
                    elif parsed.kind == "vel":
                        p = parsed.params
                        self.log_rx(
                            f"VEL {p['speed_mps']:g} m/s, {p['deg']:g}°, "
                            f"{p['omega_radps']:g} rad/s  |  {line}"
                        )
                    else:
                        self.log_rx(line)
                elif kind == "state":
                    self._handle_state(payload)
                elif kind == "wheel":
                    self._show_wheel_frame(payload)
                elif kind == "stats":
                    self._show_link_stats(payload)
                elif kind == "err":
                    self.log_err(payload)
                    if not self._reconnecting:
                        self._set_status("错误", self.palette.err)
                elif kind == "ui":
                    payload()
        except queue.Empty:
            pass
        # 超过 2s 没有新帧时,把实测帧率降为 0
        if (
            self._last_stats is not None
            and self._frame_times
            and self._wheel_hz > 0
            and time.monotonic() - self._frame_times[-1] > 2.0
        ):
            self._wheel_hz = 0.0
            self._show_link_stats(self._last_stats)
        # 曲线节流重绘(内部按 redraw_ms 限制,不会成为瓶颈)
        try:
            self.chart.maybe_redraw()
        except Exception:
            pass
        self.after(50, self._poll_queues)

    # ------------------------------------------------------------------
    # 状态切换
    # ------------------------------------------------------------------
    def _ready_buttons(self) -> None:
        self.btn_conn.configure(state="normal")
        self.btn_disc.configure(state="disabled")

    def _set_status(self, text: str, color: str | None = None) -> None:
        """更新状态栏文字与颜色。"""
        self.status_var.set(text)
        try:
            self.status_label.configure(foreground=color or self.palette.muted)
        except tk.TclError:
            pass

    def _set_connected(self) -> None:
        self._reconnecting = False
        if self.backend.kind == "ble":
            self._set_status(f"已连接 (MTU={self.backend.mtu})", self.palette.ok)
        else:
            port = getattr(self.backend, "port", "")
            if port:
                self.addr_var.set(port)
                self._last_serial_port = port
            self._set_status(f"已连接 ({self.backend.target})", self.palette.ok)
        self.btn_conn.configure(state="disabled")
        self.btn_disc.configure(state="normal")
        self.info_var.set(self.backend.detail_text or "(无链路详情)")

    def _set_disconnected(self) -> None:
        self._reconnecting = False
        self._set_status("未连接", self.palette.muted)
        self.btn_conn.configure(state="normal")
        self.btn_disc.configure(state="disabled")
        self.info_var.set("(未连接)")

    # ------------------------------------------------------------------
    # 日志
    # ------------------------------------------------------------------
    def _log(self, tag: str, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, f"[{now()}] ", "sys")
        self.log.insert(tk.END, text + "\n", tag)
        # 限制行数,避免长时间运行后 Text 无界增长
        lines = int(self.log.index("end-1c").split(".")[0])
        if lines > MAX_LOG_LINES:
            self.log.delete("1.0", f"{lines - MAX_LOG_LINES + 1}.0")
        self.log.configure(state="disabled")
        if self.autoscroll_var.get():
            self.log.see(tk.END)

    def log_sys(self, text: str) -> None:
        self._log("sys", text)

    def log_tx(self, text: str) -> None:
        self._log("tx", "TX> " + text)

    def log_rx(self, text: str) -> None:
        self._log("rx", "RX< " + text)

    def log_err(self, text: str) -> None:
        self._log("err", "!! " + text)

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", tk.END)
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------
    def on_close(self) -> None:
        # 安全:先终止定频运动,并确保底盘收到 STOP
        try:
            if self.motion is not None and self.motion.is_running:
                self.motion.stop()
        except Exception:
            pass
        try:
            fut = asyncio.run_coroutine_threadsafe(self._shutdown_sequence(), self.loop)
            fut.result(timeout=3)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        if self.chart_window is not None:
            try:
                self.chart_window.close()
            except Exception:
                pass
            self.chart_window = None
        self.destroy()

    async def _shutdown_sequence(self) -> None:
        """关闭前:等运动协程收尾(其内部会发 STOP),再断开链路。"""
        if self.motion is not None and self.motion.is_running:
            for _ in range(20):  # 最多等 2 秒让 run() 退出并发 STOP
                if not self.motion.is_running:
                    break
                await asyncio.sleep(0.1)
        try:
            await self.backend.disconnect()
        except Exception:
            pass


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
