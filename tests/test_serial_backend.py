"""SerialBackend 无硬件功能测试:用伪串口对象验证读线程/组帧/发送/断开/自动重连。

不接触真实串口;验证此前修复的“读线程无法调度自动重连”bug 是否解决。
"""
import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import ble_chassis_control.backend_serial as bs


class FakeSerial:
    """最小伪串口:读入一个字节队列;模拟阻塞读与 close。"""

    def __init__(self, port, baudrate, **kwargs):
        self.port = port
        self.baudrate = baudrate
        self.is_open = True
        self._rx_queue = []
        self._cond = threading.Condition()
        self.written = b""
        self.read_calls = 0

    # ---- 供给测试用的注入接口 ----
    def inject(self, data: bytes) -> None:
        with self._cond:
            self._rx_queue.extend(data)
            self._cond.notify_all()

    @property
    def in_waiting(self) -> int:
        with self._cond:
            return len(self._rx_queue)

    def read(self, n=1) -> bytes:
        with self._cond:
            self.read_calls += 1
            if not self._rx_queue:
                # 模拟阻塞至超时:等一会,给注入机会
                self._cond.wait(timeout=0.05)
                return b""
            out = bytes(self._rx_queue[:n])
            del self._rx_queue[:n]
            return out

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False
        with self._cond:
            self._cond.notify_all()


class FakeSerialModule:
    Serial = FakeSerial


def main() -> None:
    bs.serial = FakeSerialModule          # 替换真实 pyserial
    bs.list_ports = type("LP", (), {"comports": staticmethod(lambda: [])})()

    lines: list[str] = []
    states: list[str] = []

    def on_line(text, parsed) -> None:
        lines.append(text)

    def on_state(text) -> None:
        states.append(text)

    async def scenario() -> None:
        link = bs.SerialBackend("COM99", 9600, on_line=on_line, on_state=on_state)
        ok = await link.connect()
        assert ok, "connect 应成功"
        assert link.is_connected

        # 1) 组帧:注入 "MOT:1,2\nTEL:BAT:7.4\n" 应产出两行
        link._ser.inject(b"MOT:1,2\nTEL:BAT:7.4\n")   # type: ignore[union-attr]
        await asyncio.sleep(0.3)
        assert lines == ["MOT:1,2", "TEL:BAT:7.4"], lines
        print("1) 读线程 + 组帧(跨行/多行): OK")

        # 2) 半包 -> 补全后出整行
        link._ser.inject(b"STO")                       # type: ignore[union-attr]
        await asyncio.sleep(0.1)
        assert lines == ["MOT:1,2", "TEL:BAT:7.4"], "半包不应出整行"
        link._ser.inject(b"P\n")                       # type: ignore[union-attr]
        await asyncio.sleep(0.2)
        assert lines[-1] == "STOP", lines
        print("2) 半包拼接: OK")

        # 3) 发送 + flush(VEL 为主指令,MOT 兼容保留)
        await link.send_vel(0.5, 90, 0)
        assert link._ser.written == b"VEL:500,900,0\n", link._ser.written  # type: ignore[union-attr]
        await link.send_mot(10, -20)
        assert link._ser.written == b"VEL:500,900,0\nMOT:10,-20\n", \
            link._ser.written  # type: ignore[union-attr]
        print("3) 发送(VEL/MOT 编码 + flush): OK")

        # 4) 自动重连调度(修复验证)
        before = len(states)
        link._ser.inject(b"")                          # type: ignore[union-attr]
        # 人为触发掉线:让 read 抛异常
        class Boom(RuntimeError):
            pass

        cancelled = False
        def boom_read(n=1):  # noqa: ANN001
            nonlocal cancelled
            if not cancelled:
                raise Boom("线掉了")
            return link._ser.read(n)  # type: ignore[union-attr]

        original_read = link._ser.read                    # type: ignore[union-attr]
        link._ser.read = boom_read                        # type: ignore[union-attr]
        await asyncio.sleep(0.3)
        # 掉线后应立即出现"串口连接中断"与"5 秒后自动重连"状态
        joined = " | ".join(states[before:])
        assert "连接中断" in joined, states[before:]
        assert "自动重连" in joined, states[before:]
        print("4) 读线程掉线 → 自动重连调度(修复验证): OK")

        # 5) 断开
        cancelled = True
        link._ser.read = original_read                    # type: ignore[union-attr]
        await link.disconnect()
        assert not link.is_connected
        assert "已关闭" in states[-1], states
        print("5) 断开与资源释放: OK")

    async def send_only_scenario() -> None:
        """只下发模式:不注册 on_line,下发不受影响;收到数据静默丢弃不报错。"""
        link = bs.SerialBackend("COM99", 9600)      # 注意:无 on_line / on_state
        assert link._on_line is None and link._on_state is None
        await link.connect()
        assert link.is_connected
        await link.send_vel(0.5, 90, 0)
        await link.send_vel(0, 0, 1.57)
        await link.send_stop()
        written = link._ser.written                  # type: ignore[union-attr]
        assert written == b"VEL:500,900,0\nVEL:0,0,1570\nSTOP\n", written
        # 注入回传数据:应被静默丢弃,不抛异常
        link._ser.inject(b"TEL:BAT:7.4\njunk\n")     # type: ignore[union-attr]
        await asyncio.sleep(0.3)
        await link.disconnect()
        assert not link.is_connected
        print("6) 只下发模式(无 on_line 回调): OK")

    asyncio.run(scenario())
    asyncio.run(send_only_scenario())
    print("\nSerialBackend 功能测试全部通过 ✓")


if __name__ == "__main__":
    main()