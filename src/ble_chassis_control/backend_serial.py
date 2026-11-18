"""串口后端:通过蓝牙 SPP 虚拟串口(HC-05 等)或有线 USB-TTL 连接底盘。

HC-05 是**经典蓝牙(BR/EDR)**,不是 BLE —— Windows 配对后会创建一个
"标准串行 over 蓝牙链接" 的虚拟 COM 口,任何串口程序都可直接用。
因此本后端用 pyserial 而非 Bleak。

线程模型:
  * 读:独立线程阻塞读串口,收到字节 → 按行分帧 → 回调;
  * 写:asyncio.to_thread 包装阻塞写(HC-05 写入很快,不构成瓶颈);
  * 与 BLE 后端接口完全一致,前端无需区分。
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading

from . import config
from .link import ChassisLink, LinkError, OnLineCallback, OnStateCallback

logger = logging.getLogger(__name__)

try:
    import serial  # pyserial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - 引导安装
    serial = None  # type: ignore
    list_ports = None  # type: ignore


_BT_ADDR_RE = re.compile(r"&([0-9A-F]{12})_")


def _bluetooth_address(hwid: str) -> str | None:
    """从 BTHENUM 实例 ID 提取 12 位地址。

    返回 ``None`` = 非蓝牙口;``""`` = 蓝牙口但地址全 0(通常是过期/幽灵口)。
    """
    m = _BT_ADDR_RE.search(hwid or "")
    if not m:
        return None
    addr = m.group(1).upper()
    return addr if set(addr) != {"0"} else ""


def _format_mac(addr: str) -> str:
    return ":".join(addr[i : i + 2] for i in range(0, 12, 2))


def available_ports() -> list[tuple[str, str]]:
    """列出系统可用串口:(设备名, 描述)。

    蓝牙 SPP 口会标出真实地址;地址全 0 的旧口标为“可能失效”。
    """
    if list_ports is None:
        return []
    out = []
    for p in list_ports.comports():
        desc = p.description or ""
        hwid = (p.hwid or "").upper()
        if "BTHENUM" in hwid or "BLUETOOTH" in desc.upper():
            addr = _bluetooth_address(hwid)
            if addr:
                desc = f"{desc} [蓝牙 {_format_mac(addr)}]"
            elif addr == "":
                desc = f"{desc} [蓝牙·地址空/可能失效]"
            else:
                desc = f"{desc} [蓝牙]"
        out.append((p.device, desc))
    return out


def guess_port() -> str | None:
    """智能猜口:优先“带真实地址”的蓝牙口,再退回唯一的蓝牙/串口。"""
    ports = available_ports()
    if not ports:
        return None

    live_bt = [dev for dev, desc in ports if "[蓝牙 " in desc]
    if len(live_bt) == 1:
        return live_bt[0]

    stale_bt = [dev for dev, desc in ports if "地址空" in desc]
    if not live_bt and len(stale_bt) == 1:
        return stale_bt[0]

    if len(ports) == 1:
        return ports[0][0]
    return None


class SerialBackend(ChassisLink):
    """HC-05 / USB-TTL 串口链路。"""

    kind = "serial"

    def __init__(
        self,
        port: str | None = None,
        baudrate: int | None = None,
        *,
        on_line: OnLineCallback | None = None,
        on_state: OnStateCallback | None = None,
        on_wheel=None,
        on_stats=None,
    ) -> None:
        super().__init__(on_line=on_line, on_state=on_state, on_wheel=on_wheel, on_stats=on_stats)
        self.port = port or config.SERIAL_PORT
        self.baudrate = int(baudrate or config.SERIAL_BAUDRATE)
        self._ser: serial.Serial | None = None
        self._reader: threading.Thread | None = None
        self._stop_evt = threading.Event()
        self._reconnect_task: asyncio.Task | None = None
        self._manual_disconnect = False
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return bool(self._ser is not None and self._ser.is_open)

    @property
    def target(self) -> str:
        return f"{self.port}@{self.baudrate}"

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    async def connect(self) -> bool:
        if serial is None:
            raise LinkError("未安装 pyserial —— 请运行:uv add pyserial")
        self._manual_disconnect = False

        # 自动选口:未指定时优先蓝牙 SPP 口 / 唯一串口
        if not self.port:
            guessed = guess_port()
            if guessed:
                self.port = guessed
                self._emit(f"自动选择串口:{self.port}")
            else:
                ports = available_ports()
                if ports:
                    names = ", ".join(f"{d}({s})" for d, s in ports)
                    raise LinkError(f"检测到多个串口,请在配置或界面中指定:{names}")
                raise LinkError(
                    "未检测到任何串口:① USB-TTL 是否插好? "
                    "② HC-05 是否已在 Windows 蓝牙中配对(配对后才会出现 COM 口)?"
                )

        self._loop = asyncio.get_running_loop()
        for attempt in range(1, config.CONNECT_MAX_ATTEMPTS + 1):
            self._emit(f"打开串口第 {attempt}/{config.CONNECT_MAX_ATTEMPTS} 次 ...")
            try:
                ser = await asyncio.to_thread(
                    serial.Serial,
                    self.port,
                    self.baudrate,
                    timeout=config.SERIAL_TIMEOUT,
                    write_timeout=config.SERIAL_WRITE_TIMEOUT,
                )
                # Windows 对“已配对但模块没上电”的 SPP 口也可能打开成功;
                # 先写一条 STOP,写不出去就不算连接成功(避免假连接)。
                if config.SERIAL_PROBE_ON_CONNECT:
                    await self._probe_serial(ser)
                self._ser = ser
                self._stop_evt.clear()
                self.wheel_parser.reset()
                self._reader = threading.Thread(
                    target=self._read_loop, name="serial-reader", daemon=True
                )
                self._reader.start()
                self.detail_text = (
                    f"串口 {self.port} @ {self.baudrate}\n"
                    f"字节:8 停止位:1 校验:无 流控:无\n"
                    f"(HC-05 = 经典蓝牙 SPP 虚拟串口)"
                )
                self._emit(f"串口已打开:{self.port} @ {self.baudrate}")
                self.last_error = ""
                return True
            except Exception as exc:  # serial.SerialException 等
                self.last_error = self._friendly(exc)
                self._emit(f"  失败:{self.last_error}")
                self._ser = None
                # 被占用/权限问题:重试无意义,立即放弃
                if isinstance(exc, PermissionError) or "ACCESS IS DENIED" in str(exc).upper():
                    break
                if attempt < config.CONNECT_MAX_ATTEMPTS:
                    await asyncio.sleep(config.CONNECT_RETRY_DELAY)

        raise LinkError(f"串口打开失败:{self.last_error}")

    async def _probe_serial(self, ser) -> None:
        """打开后写一条 STOP 探测;写不出去说明蓝牙链路/模块不可用。"""
        from .protocol import Protocol

        try:
            old_timeout = getattr(ser, "write_timeout", None)
            if old_timeout is not None:
                ser.write_timeout = float(config.SERIAL_PROBE_TIMEOUT)
            try:
                await asyncio.to_thread(ser.write, Protocol.encode_stop())
            finally:
                if old_timeout is not None:
                    ser.write_timeout = old_timeout
            await asyncio.wait_for(
                asyncio.to_thread(ser.flush),
                timeout=float(config.SERIAL_PROBE_TIMEOUT),
            )
        except Exception as exc:
            try:
                await asyncio.to_thread(ser.close)
            except Exception:
                pass
            raise LinkError(
                f"串口已打开但写入探测失败:{self._friendly_write(exc, config.SERIAL_PROBE_TIMEOUT)}"
            ) from exc

    # ------------------------------------------------------------------
    # 读线程
    # ------------------------------------------------------------------
    def _read_loop(self) -> None:
        """阻塞读串口;收到字节按行分帧回调。"""
        ser = self._ser
        while not self._stop_evt.is_set() and ser is not None:
            try:
                n = ser.in_waiting or 1
                data = ser.read(n)
                if data:
                    self._feed_bytes(data)
            except Exception as exc:
                if self._stop_evt.is_set():
                    break
                self._emit(f"串口读取异常:{type(exc).__name__}: {exc}")
                self._handle_drop()
                break

    def _handle_drop(self) -> None:
        """串口掉线处理(HC-05 断开/拔线)。从读线程调用,线程安全地调度重连。"""
        self._emit("串口连接中断")
        if (
            config.RECONNECT_ON_DROP
            and not self._manual_disconnect
            and self._reconnect_task is None
            and self._loop is not None
            and self._loop.is_running()
        ):
            self._reconnect_task = asyncio.run_coroutine_threadsafe(
                self._reconnect_loop(), self._loop
            )

    async def _reconnect_loop(self) -> None:
        """掉线后持续重试,直到成功或手动断开。

        注意:这里调用 ``disconnect(manual=False)`` 只清理旧资源,
        不会取消 ``_reconnect_task``(该任务正是当前协程本身)。
        """
        delay = float(config.RECONNECT_DELAY)
        try:
            while not self._manual_disconnect:
                self._emit(f"{delay:g} 秒后自动重连串口 ...")
                await asyncio.sleep(delay)
                if self._manual_disconnect:
                    break
                try:
                    await self.disconnect(manual=False)
                    await self.connect()
                    self._emit("串口自动重连成功")
                    return
                except LinkError as exc:
                    self._emit(f"自动重连失败:{exc}({delay:g} 秒后重试)")
        finally:
            self._reconnect_task = None  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------
    async def send(self, data: bytes) -> None:
        if not self.is_connected:
            raise LinkError("串口未连接")
        ser = self._ser
        try:
            await asyncio.to_thread(ser.write, data)
            # flush 在蓝牙虚拟串口上可能无限阻塞,单独加超时
            await asyncio.wait_for(
                asyncio.to_thread(ser.flush),
                timeout=float(config.SERIAL_WRITE_TIMEOUT),
            )
            self.last_error = ""
        except Exception as exc:
            # 写超时/写失败 = 链路已不可用。主动断开并调度重连,
            # 否则 is_connected 会一直是 True(假连接),后续每次发送都白等 2s。
            self.last_error = self._friendly_write(exc)
            self._emit(f"串口写入失败:{self.last_error}")
            await self._drop_link()
            raise LinkError(f"串口写入失败:{self.last_error}") from exc

    async def _drop_link(self) -> None:
        """写失败后主动关闭串口并调度重连。"""
        ser, self._ser = self._ser, None
        self._stop_evt.set()
        if ser is not None:
            try:
                await asyncio.to_thread(ser.close)
            except Exception:
                pass
        if self._reader and self._reader.is_alive():
            await asyncio.to_thread(self._reader.join, 1.0)
        self._reader = None
        self._handle_drop()

    # ------------------------------------------------------------------
    # 断开
    # ------------------------------------------------------------------
    async def disconnect(self, manual: bool = True) -> None:
        self._manual_disconnect = manual
        # 只有手动断开才取消重连;重连协程内部清理时不能取消自身
        if manual and self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        self._stop_evt.set()
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                await asyncio.to_thread(ser.close)
            except Exception:
                pass
        if self._reader and self._reader.is_alive():
            await asyncio.to_thread(self._reader.join, 1.0)
        self._reader = None
        self._emit("串口已关闭")

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _friendly_write(exc: Exception, timeout: float | None = None) -> str:
        """把写超时翻译成可操作的提示。"""
        msg = str(exc) or type(exc).__name__
        if isinstance(exc, TimeoutError) or "TIMEOUT" in msg.upper():
            t = config.SERIAL_WRITE_TIMEOUT if timeout is None else timeout
            return (
                f"写超时({t:g}s):"
                "HC-05 可能未连接、模块 UART 缓冲已满(MCU 未取数)或蓝牙驱动卡住。"
                "已断开并尝试自动重连;请检查模块 LED、MCU 供电与 TX/RX、两端 115200"
            )
        return f"{type(exc).__name__}:{msg}"

    @staticmethod
    def _friendly(exc: Exception) -> str:
        msg = str(exc)
        name = type(exc).__name__
        up = msg.upper()
        if "ACCESS IS DENIED" in up or "PERMISSION" in up:
            return f"串口被占用({msg}):请关闭其他串口程序(VOFA+ / 串口助手 / Arduino IDE)后重试"
        if "FILE NOT FOUND" in up or "COULD NOT OPEN PORT" in up:
            return (
                f"串口不存在({msg}):① HC-05 是否已在 Windows 蓝牙中配对?"
                "② 设备管理器 → 端口(COM 和 LPT)确认口号"
            )
        return f"{name}:{msg}"
