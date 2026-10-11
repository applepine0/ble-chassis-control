"""冒烟测试:协议编解码 + 后端/前端可加载、GUI 可创建(不连硬件)。"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ble_chassis_control import config          # noqa: E402
from ble_chassis_control.protocol import (      # noqa: E402
    Protocol, ParsedMessage, decode_line,
)
from ble_chassis_control.backend import BLEBackend  # noqa: E402


def test_protocol() -> None:
    mot = Protocol.encode_mot(25.0, -30.5)
    assert mot == b"MOT:25,-30.5\n", mot
    assert Protocol.encode_stop() == b"STOP\n"
    assert Protocol.encode_set("MAX_SPEED", 80) == b"SET:MAX_SPEED:80\n"
    assert Protocol.encode_raw("hi") == b"hi\n"
    assert Protocol.encode_raw("hi\n") == b"hi\n"

    p = Protocol.parse_line("MOT:100,-100")
    assert p.kind == "mot" and p.params == {"left": 100.0, "right": -100.0}, p

    p = Protocol.parse_line("TEL:BAT:7.4,SPD_L:12.3")
    assert p.kind == "tel" and p.params["values"] == {"BAT": 7.4, "SPD_L": 12.3}, p

    p = Protocol.parse_line("STOP")
    assert p.kind == "stop", p

    p = Protocol.parse_line("hello world")
    assert p.kind == "unknown", p

    p = Protocol.parse_line("MOT:abc,1")
    assert p.error, "应当报参数错误"

    assert decode_line(b"abc") == "abc"

    # 组帧模拟:通知回调按整行分发
    backend = BLEBackend()
    lines: list[tuple[str, ParsedMessage]] = []
    backend._on_line = lambda line, parsed: lines.append((line, parsed))
    backend._rx_buffer = b""
    backend._on_notify(None, bytearray(b"IND:0\r"))
    assert lines == [], "未到换行不应分发"
    backend._on_notify(None, bytearray(b"\nmore\n"))
    assert [l for l, _ in lines] == ["IND:0\r", "more"], lines

    print("protocol + backend 组帧: OK")


def test_backend_instance() -> None:
    b = BLEBackend(address="00:11:22:33:44:55")
    assert b.address == "00:11:22:33:44:55"
    assert b.is_connected is False
    assert b.write_uuid is None
    print("backend 实例化: OK")


def test_gui_headless() -> None:
    from ble_chassis_control.frontend import App

    app = App()
    app.update()
    app.update_idletasks()
    # 不真正 mainloop,提交一个空协程并轮询队列,验证后台循环活着
    fut = None
    import asyncio

    fut = asyncio.run_coroutine_threadsafe(asyncio.sleep(0.05), app.loop)
    fut.result(timeout=2)
    app.on_close()  # 触发销毁与循环停止
    print("GUI 冒烟(创建/调度/关闭): OK")


if __name__ == "__main__":
    test_protocol()
    test_backend_instance()
    test_gui_headless()
    print("\n全部冒烟测试通过 ✓")