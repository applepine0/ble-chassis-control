"""链路抽象层:统一 BLE 与串口(HC-05 等)两种底层的接口。

设计目的:
  * 前端只依赖 ``ChassisLink`` 抽象,不关心底层是 BLE 还是串口;
  * 换模块/换链路时,只增删一个后端实现,前端零修改;
  * 所有 IO 方法均为 async(串口后端用 asyncio.to_thread 包装阻塞 IO),
    这样前端的 asyncio 后台线程模型对两种后端完全一致。
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Callable, Optional

from .protocol import ParsedMessage

logger = logging.getLogger(__name__)

OnLineCallback = Callable[[str, ParsedMessage], None]
OnStateCallback = Callable[[str], None]


class LinkError(Exception):
    """统一的链路错误,携带面向用户的中文提示。"""


# 兼容旧命名(既有代码/脚本可能引用)
BLEError = LinkError


class ChassisLink(ABC):
    """底盘链路的统一接口。

    实现者需保证:
      * ``connect()`` 幂等、可重试,失败抛 ``LinkError``;
      * ``send_*`` 在未连接时抛 ``LinkError``;
      * 收到的每一"行"通过 ``on_line`` 回调上报(已解析)。
    """

    #: 面向用户显示的链路类型名(前端标题/状态栏用)
    kind: str = "link"

    def __init__(
        self,
        *,
        on_line: OnLineCallback | None = None,
        on_state: OnStateCallback | None = None,
    ) -> None:
        self._on_line = on_line
        self._on_state = on_state
        # 公开只读信息(前端展示用)
        self.detail_text: str = ""     # 链路细节(GATT 树 / 串口参数)
        self.mtu: int = 0              # BLE 有值;串口后端为 0
        self.last_error: str = ""
        self._rx_buffer = b""

    # ------------------------------------------------------------------
    # 状态与回调
    # ------------------------------------------------------------------
    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """当前是否已连接。"""

    @property
    def target(self) -> str:
        """链路目标描述(地址 / COM 口),前端展示用。"""
        return ""

    def _emit(self, text: str) -> None:
        if self._on_state:
            try:
                self._on_state(text)
            except Exception:
                logger.exception("on_state 回调异常")

    def _emit_line(self, text: str) -> None:
        """把一行原始文本交给上层(已做协议解析)。"""
        from .protocol import Protocol  # 延迟导入避免循环

        parsed = Protocol.parse_line(text)
        if self._on_line:
            try:
                self._on_line(text, parsed)
            except Exception:
                logger.exception("on_line 回调异常")

    def _feed_bytes(self, data: bytes) -> None:
        """按换行分帧;供各后端在收到数据时调用。"""
        from . import config
        from .protocol import decode_line

        delim = config.LINE_END.encode(config.ENCODING)
        self._rx_buffer += data
        while delim in self._rx_buffer:
            raw_line, _, self._rx_buffer = self._rx_buffer.partition(delim)
            if raw_line:
                self._emit_line(decode_line(raw_line))

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    @abstractmethod
    async def connect(self) -> bool:
        """建立连接;成功返回 True,失败抛 LinkError。"""

    @abstractmethod
    async def disconnect(self, manual: bool = True) -> None:
        """断开连接并释放资源。"""

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------
    @abstractmethod
    async def send(self, data: bytes) -> None:
        """发送原始字节。"""

    async def send_text(self, text: str) -> None:
        from .protocol import Protocol

        await self.send(Protocol.encode_raw(text))

    async def send_vel(self, speed_mps: float, deg: float, omega_radps: float) -> None:
        """速度矢量指令(主指令):速度大小 m/s + 方向 deg + 自转 rad/s。"""
        from .protocol import Protocol

        await self.send(Protocol.encode_vel(speed_mps, deg, omega_radps))

    async def send_mot(self, left: float, right: float) -> None:
        """差速指令(兼容旧接口)。"""
        from .protocol import Protocol

        await self.send(Protocol.encode_mot(left, right))

    async def send_stop(self) -> None:
        from .protocol import Protocol

        await self.send(Protocol.encode_stop())

    async def send_set(self, name: str, value: float) -> None:
        from .protocol import Protocol

        await self.send(Protocol.encode_set(name, value))
