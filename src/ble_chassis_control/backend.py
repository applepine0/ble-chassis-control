"""BLE 后端:基于 Bleak 封装连接、GATT 自动识别、数据收发。

针对 Windows 蓝牙栈的加固点(本机实测结论):
  * JDY-23 在 Windows 缓存中为 public 地址类型 —— 显式指定,避免 random 导致找不到设备;
  * GATT 服务枚举会整段挂起(timeout)—— 用 ``use_cached_services=False`` 强制 UNCACHED,
    配合更长的操作超时与多轮重试;
  * 模块不响应配对 —— 连接阶段不做 pairing;
  * 广播包已确认 Service FFE0 / 特征 FFE1(write+notify),见 config.py。

⚠️ 实测结论:JDY-23(V81-02 固件)与 Windows 的配对/安全层不兼容,无法完成
GATT 服务枚举。若使用该模块请改用 SerialBackend(HC-05 经典蓝牙串口)。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

from bleak import BleakClient, BleakGATTCharacteristic, BleakScanner

from . import config
from .link import BLEError, ChassisLink, LinkError, OnLineCallback, OnStateCallback

logger = logging.getLogger(__name__)


class BLEBackend(ChassisLink):
    """封装全部 BLE 交互;所有 IO 方法均为 async,须在 asyncio 事件循环里调用。"""

    kind = "ble"

    def __init__(
        self,
        address: str | None = None,
        *,
        on_line: OnLineCallback | None = None,
        on_state: OnStateCallback | None = None,
        on_wheel=None,
        on_stats=None,
    ) -> None:
        super().__init__(
            on_line=on_line, on_state=on_state, on_wheel=on_wheel, on_stats=on_stats
        )
        self.address = (address or config.DEVICE_ADDRESS).upper()
        self._client: Optional[BleakClient] = None
        self._write_char: Optional[BleakGATTCharacteristic] = None
        self._notify_char: Optional[BleakGATTCharacteristic] = None
        # 公开只读信息
        self.write_uuid: Optional[str] = None
        self.notify_uuid: Optional[str] = None
        self.services_text: str = ""
        self._reconnect_task: Optional[asyncio.Task] = None
        self._manual_disconnect = False

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------
    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    @property
    def target(self) -> str:
        return self.address

    @property
    def detail_text(self) -> str:  # type: ignore[override]
        return self.services_text

    @detail_text.setter
    def detail_text(self, value: str) -> None:
        self.services_text = value

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------
    async def connect(self) -> bool:
        """带重试的连接。成功返回 True,全部失败抛 BLEError。"""
        self._manual_disconnect = False
        last: Exception | None = None
        for attempt in range(1, config.CONNECT_MAX_ATTEMPTS + 1):
            self._emit(f"连接第 {attempt}/{config.CONNECT_MAX_ATTEMPTS} 次 ...")
            try:
                await self._connect_once()
                return True
            except Exception as exc:
                last = exc
                self.last_error = self._friendly(exc, connect=True)
                self._emit(f"  失败:{self.last_error}")
                await self._close_client()
                if attempt < config.CONNECT_MAX_ATTEMPTS:
                    await asyncio.sleep(config.CONNECT_RETRY_DELAY)

        raise BLEError(f"连接失败({config.CONNECT_MAX_ATTEMPTS} 次):{self.last_error}")

    async def _connect_once(self) -> None:
        # 1) 先扫描拿 BLEDevice(Windows 下比裸地址可靠)
        device = await BleakScanner.find_device_by_address(
            self.address, timeout=config.SCAN_TIMEOUT
        )
        if device is None:
            raise BLEError(f"未能在 {config.SCAN_TIMEOUT}s 内扫描到 {self.address}")

        # 2) 建立客户端:public 地址类型 + UNCACHED 读 GATT + 长操作超时
        client = BleakClient(
            device,
            timeout=config.CONNECT_OP_TIMEOUT,
            winrt={
                "use_cached_services": config.USE_CACHED_SERVICES,
                "address_type": config.ADDRESS_TYPE,
            },
            disconnected_callback=self._on_unsolicited_disconnect,
        )
        await client.connect()
        self._client = client
        try:
            self.mtu = int(client.mtu_size)
        except Exception:
            self.mtu = 0

        # 3) 解析并记住收发特征
        self._resolve_characteristics(client)

        # 4) 订阅通知,开始组帧回传
        self._rx_buffer = b""
        if self._notify_char is not None:
            await client.start_notify(self._notify_char, self._on_notify)
        else:
            self._emit("警告:未找到 notify 特征,只能下发、无法接收回传")

        self._emit(
            f"连接成功 MTU={self.mtu} 写={self.write_uuid} 通知={self.notify_uuid}"
        )

    def _resolve_characteristics(self, client: BleakClient) -> None:
        """按优先级解析收发特征:已知 UUID 精确匹配 -> 启发式查找。"""
        known_sets = [
            (config.WRITE_UUID, config.NOTIFY_UUID),
            (config.NUS_WRITE_UUID, config.NUS_NOTIFY_UUID),
        ]
        chars: dict[str, BleakGATTCharacteristic] = {}
        for service in client.services:
            for ch in service.characteristics:
                chars[ch.uuid.lower()] = ch
                if ch.short_uuid:
                    chars.setdefault(ch.short_uuid.lower(), ch)

        def lookup(wanted: str) -> BleakGATTCharacteristic | None:
            return chars.get(wanted.lower())

        write_ch = notify_ch = None
        for w, n in known_sets:
            if write_ch is None:
                write_ch = lookup(w)
            if notify_ch is None:
                notify_ch = lookup(n)
            if write_ch and notify_ch:
                break

        # 启发式兜底
        if write_ch is None or notify_ch is None:
            services = list(client.services)
            services.sort(key=lambda s: _service_rank(s.uuid.lower()))
            for svc in services:
                for ch in svc.characteristics:
                    props = set(ch.properties)
                    if write_ch is None and (
                        "write" in props or "write-without-response" in props
                    ):
                        write_ch = ch
                    if notify_ch is None and (
                        "notify" in props or "indicate" in props
                    ):
                        notify_ch = ch
                if write_ch and notify_ch:
                    break
            # 别名类模块(FFE1 同时承担读写)下,通知特征可能就是这个唯一特征
            if notify_ch is None and write_ch is not None and (
                "notify" in set(write_ch.properties)
            ):
                notify_ch = write_ch

        if write_ch is None:
            raise BLEError("未找到可写特征,请核对模块 UUID")
        self._write_char = write_ch
        self._notify_char = notify_ch
        self.write_uuid = write_ch.uuid
        self.notify_uuid = notify_ch.uuid if notify_ch else None

        # 服务树快照(供界面展示)
        lines = []
        for svc in client.services:
            lines.append(f"服务 {svc.uuid} ({svc.description})")
            for ch in svc.characteristics:
                mark = []
                if ch == write_ch:
                    mark.append("<-写")
                if notify_ch and ch == notify_ch:
                    mark.append("<-通知")
                lines.append(f"  特征 {ch.uuid} props={','.join(ch.properties)} {''.join(mark)}")
        self.services_text = "\n".join(lines)

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------
    async def send(self, data: bytes) -> None:
        if not self.is_connected or self._write_char is None:
            raise BLEError("未连接")
        preferred = config.WRITE_WITH_RESPONSE
        if preferred is None:
            preferred = "write-without-response" not in set(
                self._write_char.properties
            )
        last: Exception | None = None
        for mode in (preferred, not preferred):
            try:
                await self._client.write_gatt_char(
                    self._write_char, data, response=mode
                )
                return
            except Exception as exc:
                last = exc
        raise BLEError(f"写入失败:{last}") from last

    # ------------------------------------------------------------------
    # 接收
    # ------------------------------------------------------------------
    def _on_notify(self, _char: BleakGATTCharacteristic, data: bytearray) -> None:
        """Bleak 通知回调(事件循环线程内)。分帧与解析由基类统一处理。"""
        self._feed_bytes(bytes(data))

    # ------------------------------------------------------------------
    # 断开 / 自动重连
    # ------------------------------------------------------------------
    def _on_unsolicited_disconnect(self, _client: BleakClient) -> None:
        self._emit("连接意外断开")
        if (
            config.RECONNECT_ON_DROP
            and not self._manual_disconnect
            and self._reconnect_task is None
        ):
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        self._emit("5 秒后自动重连 ...")
        await asyncio.sleep(5.0)
        try:
            await self.connect()
        except BLEError as exc:
            self._emit(f"自动重连失败:{exc}")
        finally:
            self._reconnect_task = None

    async def disconnect(self, manual: bool = True) -> None:
        self._manual_disconnect = manual
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        await self._close_client()
        self._emit("已断开")

    async def _close_client(self) -> None:
        client, self._client = self._client, None
        self._write_char = None
        self._notify_char = None
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    @staticmethod
    def _friendly(exc: Exception, *, connect: bool) -> str:
        name = type(exc).__name__
        msg = str(exc) or name
        if isinstance(exc, TimeoutError) or "TimeoutError" in name:
            return (
                "GATT 读取超时:可能原因 — ① 模块正被手机等其他设备占用;"
                "② 蓝牙适配器异常(尝试重启电脑蓝牙);③ 模块进入低功耗休眠(AT+STARTEN 0 唤醒)"
            )
        if "was not found" in msg:
            return f"设备未找到:请确认模块已上电且处于广播状态({config.SCAN_TIMEOUT}s 内未出现)"
        return f"{name}:{msg}"


def _service_rank(uuid: str) -> int:
    """启发式优先级:FFE0 / NUS 服务优先。"""
    u = uuid.lower()
    if "ffe0" in u:
        return 0
    if config.NUS_SERVICE_UUID.lower() in u:
        return 1
    return 2