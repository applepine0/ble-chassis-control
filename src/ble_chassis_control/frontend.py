"""tkinter 上位机界面:连接管理、底盘控制、收发日志。

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
from tkinter import messagebox, ttk
from typing import Optional

from . import config
from .backend import BLEBackend, BLEError
from .backend_serial import SerialBackend, available_ports
from .link import ChassisLink, LinkError
from .protocol import ParsedMessage

# 界面配色
C_SYS = "#808080"
C_TX = "#1565C0"
C_RX = "#2E7D32"
C_ERR = "#C62828"
C_BG = "#F5F5F5"


def now() -> str:
    return time.strftime("%H:%M:%S")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("底盘上位机 — HC-05 串口 / BLE 透传控制台")
        self.geometry("900x640")
        self.minsize(780, 540)
        self.configure(bg=C_BG)

        # 异步基础设施
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(
            target=self._loop_entry, name="asyncio-loop", daemon=True
        )
        self.loop_thread.start()

        # 当前链路(默认串口;由界面单选切换)
        self.backend: ChassisLink = self._make_link(config.LINK_KIND)
        self.rx_queue: "queue.Queue[tuple]" = queue.Queue()

        self._build_ui()
        self.after(50, self._poll_queues)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------------------------------------------------------
    # 链路构建
    # ------------------------------------------------------------------
    def _make_link(self, kind: str) -> ChassisLink:
        """按类型创建链路对象(BLE / 串口)。"""
        if kind == "ble":
            return BLEBackend(on_line=self._on_line_cb, on_state=self._on_state_cb)
        return SerialBackend(on_line=self._on_line_cb, on_state=self._on_state_cb)

    def _rebuild_link(self) -> None:
        """切换链路类型:先断开旧链路,再创建新的。"""
        old = self.backend
        if old.is_connected:
            self._submit(old.disconnect())
        self.backend = self._make_link(self.link_kind.get())
        self.log_sys(f"链路已切换为:{'BLE' if self.link_kind.get() == 'ble' else '串口'}")
        self._sync_target_field()

    # ------------------------------------------------------------------
    # 异步循环
    # ------------------------------------------------------------------
    def _loop_entry(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _submit(self, coro, *, on_success=None, on_error=None) -> None:
        """把协程提交到后台事件循环;结果/异常经线程安全队列送回 GUI 线程。"""
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def done(f: asyncio.Future) -> None:  # 在事件循环线程执行
            try:
                result = f.result()
                if on_success is not None:
                    self.rx_queue.put(("ui", lambda r=result: on_success(r)))
            except BLEError as exc:
                # 注意:必须在 lambda 创建时绑定值 —— except 变量在块结束时会被
                # Python 3 销毁,延迟引用会抛 NameError(实测踩过)
                err = str(exc)
                if on_error is not None:
                    self.rx_queue.put(("ui", lambda e=err: on_error(e)))
                else:
                    self.rx_queue.put(("err", err))
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                if on_error is not None:
                    self.rx_queue.put(("ui", lambda e=err: on_error(e)))
                else:
                    self.rx_queue.put(("err", err))

        fut.add_done_callback(done)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 6}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        # 顶部:链路选择 + 连接栏
        top = ttk.Frame(self, padding=pad["padx"])
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(3, weight=1)

        # 链路类型:串口(HC-05)/ BLE
        self.link_kind = tk.StringVar(value=config.LINK_KIND)
        ttk.Radiobutton(
            top, text="串口 (HC-05)", value="serial",
            variable=self.link_kind, command=self._rebuild_link,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            top, text="BLE", value="ble",
            variable=self.link_kind, command=self._rebuild_link,
        ).grid(row=0, column=1, sticky="w", padx=(6, 12))

        self.target_label = ttk.Label(top, text="串口:")
        self.target_label.grid(row=0, column=2, sticky="w")
        self.addr_var = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.addr_var, width=22).grid(
            row=0, column=3, sticky="ew", padx=6
        )
        ttk.Button(top, text="刷新口", width=8, command=self._refresh_ports).grid(
            row=0, column=4, padx=2
        )
        self.btn_conn = ttk.Button(top, text="连接", command=self.on_connect)
        self.btn_conn.grid(row=0, column=5, padx=6)
        self.btn_disc = ttk.Button(
            top, text="断开", command=self.on_disconnect, state="disabled"
        )
        self.btn_disc.grid(row=0, column=6, padx=2)
        ttk.Button(top, text="自检", command=self.on_self_check).grid(
            row=0, column=7, padx=2
        )
        self.status_var = tk.StringVar(value="未连接")
        ttk.Label(top, textvariable=self.status_var, foreground=C_SYS).grid(
            row=0, column=8, sticky="e", padx=6
        )

        # 第二行:串口列表/提示
        self.port_hint = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.port_hint, foreground=C_SYS).grid(
            row=1, column=0, columnspan=9, sticky="w", pady=(4, 0)
        )
        self._sync_target_field()
        self._refresh_ports()

        # 中部:控制 + 日志
        mid = ttk.Frame(self, padding=8)
        mid.grid(row=1, column=0, sticky="nsew")
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)

        ctrl = ttk.LabelFrame(mid, text=" 底盘控制 ", padding=8)
        ctrl.grid(row=0, column=0, sticky="n", padx=(0, 8))

        ttk.Label(ctrl, text="左轮速度 %").grid(row=0, column=0, sticky="w")
        self.left_var = tk.DoubleVar(value=0.0)
        ttk.Scale(
            ctrl,
            from_=-100, to=100, variable=self.left_var,
            length=200, command=self._scale_drag,
        ).grid(row=0, column=1, padx=6, pady=4)
        self.left_spin = ttk.Spinbox(
            ctrl, from_=-100, to=100, textvariable=self.left_var, width=8
        )
        self.left_spin.grid(row=0, column=2)

        ttk.Label(ctrl, text="右轮速度 %").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.right_var = tk.DoubleVar(value=0.0)
        ttk.Scale(
            ctrl,
            from_=-100, to=100, variable=self.right_var,
            length=200, command=self._scale_drag,
        ).grid(row=1, column=1, padx=6, pady=4)
        self.right_spin = ttk.Spinbox(
            ctrl, from_=-100, to=100, textvariable=self.right_var, width=8
        )
        self.right_spin.grid(row=1, column=2)

        btns = ttk.Frame(ctrl)
        btns.grid(row=2, column=0, columnspan=3, pady=10, sticky="ew")
        self.btn_mot = ttk.Button(btns, text="发送速度", command=self.on_send_mot)
        self.btn_mot.pack(side="left", expand=True, fill="x", padx=4)
        self.btn_stop = ttk.Button(btns, text="急停", command=self.on_stop)
        self.btn_stop.pack(side="left", expand=True, fill="x", padx=4)

        ttk.Separator(ctrl).grid(row=3, column=0, columnspan=3, sticky="ew", pady=6)

        # 通用 SET 命令
        ttk.Label(ctrl, text="SET 命令:").grid(row=4, column=0, sticky="w")
        self.set_name = ttk.Entry(ctrl, width=10)
        self.set_name.grid(row=4, column=1, sticky="ew", padx=6)
        self.set_name.insert(0, "MAX_SPEED")
        self.set_val = ttk.Entry(ctrl, width=8)
        self.set_val.grid(row=4, column=2)
        self.set_val.insert(0, "80")
        self.btn_set = ttk.Button(ctrl, text="发送 SET", command=self.on_send_set)
        self.btn_set.grid(row=5, column=0, columnspan=3, sticky="ew", pady=8)

        # 手动透传行
        ttk.Label(ctrl, text="手动发送:").grid(row=6, column=0, sticky="w")
        self.raw_var = tk.StringVar()
        ttk.Entry(ctrl, textvariable=self.raw_var).grid(
            row=6, column=1, columnspan=2, sticky="ew", padx=6
        )
        self.btn_raw = ttk.Button(ctrl, text="发送", command=self.on_send_raw)
        self.btn_raw.grid(row=7, column=0, columnspan=3, sticky="ew", pady=8)

        # 右侧:日志
        logf = ttk.LabelFrame(mid, text=" 通信日志 ", padding=4)
        logf.grid(row=0, column=1, sticky="nsew")
        logf.columnconfigure(0, weight=1)
        logf.rowconfigure(0, weight=1)

        self.log = tk.Text(
            logf, state="disabled", wrap="word",
            font=("Consolas", 10), bg="#FFFFFF",
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(logf, command=self.log.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)
        for tag, color in (("sys", C_SYS), ("tx", C_TX), ("rx", C_RX), ("err", C_ERR)):
            self.log.tag_configure(tag, foreground=color)

        ttk.Button(logf, text="清空日志", width=10, command=self.clear_log).grid(
            row=1, column=0, columnspan=2, sticky="e", pady=4
        )

        # 底部:GATT 信息
        self.info_var = tk.StringVar(value="GATT: (未连接)")
        ttk.Label(self, textvariable=self.info_var, foreground=C_SYS).grid(
            row=2, column=0, sticky="w", padx=8, pady=4
        )

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------
    def _sync_target_field(self) -> None:
        """切换链路时,同步"目标"输入框的标签与默认值。"""
        if self.link_kind.get() == "ble":
            self.target_label.configure(text="模块地址:")
            self.addr_var.set(config.DEVICE_ADDRESS)
        else:
            self.target_label.configure(text="串口号:")
            self.addr_var.set(config.SERIAL_PORT)

    def _refresh_ports(self) -> None:
        """扫描可用串口并提示。"""
        if self.link_kind.get() != "serial":
            self.port_hint.set("")
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
        # 若未指定且系统只有一个口,自动填入
        if not self.addr_var.get().strip() and len(ports) == 1:
            self.addr_var.set(ports[0][0])

    def _scale_drag(self, _=None) -> None:
        # 滑块拖动时不下发,避免刷屏;仅同步显示
        pass

    def on_connect(self) -> None:
        # 防护:已连接 / 连接中时忽略重复点击,避免二次打开串口自锁
        if self.backend.is_connected:
            self.log_sys("已连接,忽略重复的\"连接\"点击")
            return
        value = self.addr_var.get().strip()
        if self.link_kind.get() == "ble":
            self.backend.address = value.upper()   # type: ignore[attr-defined]
        else:
            self.backend.port = value             # type: ignore[attr-defined]
            self.backend.baudrate = config.SERIAL_BAUDRATE  # type: ignore[attr-defined]
        self.log_sys(f"连接目标:{self.backend.kind} / {self.backend.target or '(自动)'}")
        self.btn_conn.configure(state="disabled")
        self.btn_disc.configure(state="disabled")
        self.status_var.set("连接中 ...")
        self._submit(
            self.backend.connect(),
            # 注意:成功回调只调 _set_connected —— 不能带 _ready_buttons,
            # 那会把连接按钮错误地恢复为可点(实测 bug)
            on_success=lambda _r: self._set_connected(),
            on_error=lambda err: (
                self.status_var.set("连接失败"),
                self.log_err(err),
                self._ready_buttons(),
            ),
        )

    def on_disconnect(self) -> None:
        self._submit(self.backend.disconnect())
        self._set_disconnected()

    def on_self_check(self) -> None:
        """一键自检:串口列出可用口;BLE 扫描目标模块广播。"""
        if self.link_kind.get() == "serial":
            self._refresh_ports()
            self.log_sys("串口自检完成(见上方可用串口列表)")
            return
        address = self.addr_var.get().strip().upper()
        self.backend.address = address  # type: ignore[attr-defined]
        self.log_sys(f"自检:扫描 {address} (8s)...")
        self.status_var.set("自检中 ...")
        self._submit(
            self._do_self_check(),
            on_success=lambda _r: self.status_var.set("未连接"),
            on_error=lambda err: (
                self.status_var.set("自检失败"),
                self.log_err(err),
            ),
        )

    async def _do_self_check(self) -> None:
        from bleak import BleakScanner

        target = self.backend.address
        devices = await BleakScanner.discover(timeout=8.0, return_adv=True)
        hits = 0
        for addr, (dev, adv) in devices.items():
            name = (dev.name or "").upper()
            if addr.upper() == target or name == "JDY-23":
                hits += 1
                uuids = ", ".join(adv.service_uuids) or "(无服务广播)"
                self.log_sys(
                    f"发现 {addr}  name={dev.name!r} RSSI={adv.rssi} service=[{uuids}]"
                )
        if hits == 0:
            self.status_var.set("未发现目标模块")
            self.log_err(
                f"未发现 {target}(或名 JDY-23):请确认模块已上电并在广播;"
                "若在广播仍连不上,按 README 排查清单检查占用/休眠/iBeacon"
            )
        else:
            self.log_sys("自检完成:模块在广播;如仍连接超时,请执行 README 排查清单")

    def on_send_mot(self) -> None:
        try:
            left = float(self.left_var.get())
            right = float(self.right_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("输入错误", "速度必须是数字")
            return
        self.log_tx(f"MOT {left:g},{right:g}")
        self._submit(self.backend.send_mot(left, right))

    def on_stop(self) -> None:
        self.left_var.set(0.0)
        self.right_var.set(0.0)
        self.log_tx("STOP 急停")
        self._submit(self.backend.send_stop())

    def on_send_set(self) -> None:
        name = self.set_name.get().strip()
        try:
            value = float(self.set_val.get())
        except ValueError:
            messagebox.showwarning("输入错误", "SET 数值必须是数字")
            return
        self.log_tx(f"SET {name}={value:g}")
        self._submit(self.backend.send_set(name, value))

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

    def _poll_queues(self) -> None:
        try:
            while True:
                kind, payload = self.rx_queue.get_nowait()
                if kind == "line":
                    line, parsed = payload
                    if parsed.kind == "tel":
                        kv = ", ".join(f"{k}={v:g}" for k, v in parsed.params["values"].items())
                        self.log_rx(f"TEL {kv}  |  {line}")
                    else:
                        self.log_rx(line)
                elif kind == "state":
                    self.log_sys(payload)
                elif kind == "err":
                    self.log_err(payload)
                    self.status_var.set("错误")
                elif kind == "ui":
                    payload()
        except queue.Empty:
            pass
        self.after(50, self._poll_queues)

    # ------------------------------------------------------------------
    # 状态切换
    # ------------------------------------------------------------------
    def _ready_buttons(self) -> None:
        self.btn_conn.configure(state="normal")
        self.btn_disc.configure(state="disabled")

    def _set_connected(self) -> None:
        if self.backend.kind == "ble":
            self.status_var.set(f"已连接 (MTU={self.backend.mtu})")
        else:
            self.status_var.set(f"已连接 ({self.backend.target})")
        self.btn_conn.configure(state="disabled")
        self.btn_disc.configure(state="normal")
        self.info_var.set(self.backend.detail_text or "(无链路详情)")

    def _set_disconnected(self) -> None:
        self.status_var.set("未连接")
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
        self.log.configure(state="disabled")
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
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self.backend.disconnect(), self.loop
            )
            fut.result(timeout=3)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.destroy()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()