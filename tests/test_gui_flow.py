"""GUI 功能级测试:用模拟后端跑通「连接 → 发送 MOT → 收到 TEL 回传 → 日志渲染」。

不接触硬件;验证前端线程模型、队列回传、按钮逻辑与日志渲染整条链路。
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ble_chassis_control.protocol import Protocol  # noqa: E402


class FakeBackend:
    """模拟 BLEBackend 的异步接口。"""

    def __init__(self) -> None:
        self.address = "7B:B7:05:04:23:A2"
        self.port = ""
        self.baudrate = 9600
        self.mtu = 23
        self.detail_text = "服务 0000ffe0\n  特征 0000ffe1"
        self.sent: list[str] = []
        self.connected = False

    kind = "ble"

    @property
    def target(self) -> str:
        return self.address

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> bool:
        await asyncio.sleep(0.05)
        self.connected = True
        return True

    async def disconnect(self) -> None:
        self.connected = False

    async def send_vel(self, speed_mps: float, deg: float, omega_radps: float) -> None:
        self.sent.append(f"VEL:{speed_mps},{deg},{omega_radps}")

    async def send_mot(self, left: float, right: float) -> None:
        self.sent.append(f"MOT:{left},{right}")

    async def send_stop(self) -> None:
        self.sent.append("STOP")

    async def send_set(self, name: str, value: float) -> None:
        self.sent.append(f"SET:{name}:{value}")

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


def pump(app, seconds: float = 0.8) -> None:
    """轮询 tk 事件 + 让后台并线程跑,直到队列被消费。"""
    end = time.time() + seconds
    while time.time() < end:
        app.update()
        time.sleep(0.02)


def run() -> None:
    from ble_chassis_control.frontend import App

    app = App()
    fake = FakeBackend()
    app.backend = fake
    try:
        # 1) 连接
        app.on_connect()
        pump(app, 1.0)
        assert fake.connected, "模拟后端应已连接"
        assert "已连接 (MTU=23)" in app.status_var.get(), app.status_var.get()
        assert "0000ffe1" in app.info_var.get(), app.info_var.get()
        print("1) 连接流程 + 状态/GATT 展示: OK")

        # 2) 发送速度矢量 (VEL)
        app.speed_var.set(0.5)
        app.deg_var.set(90.0)
        app.omega_var.set(-0.5)
        app.on_send_vel()
        pump(app, 0.5)
        assert fake.sent == ["VEL:0.5,90.0,-0.5"], fake.sent
        assert "TX> VEL 0.5 m/s, 90°, -0.5 rad/s" in app.log.get("1.0", "end"), \
            app.log.get("1.0", "end")
        print("2) VEL 速度矢量发送 + TX 日志: OK")

        # 3) 急停
        app.on_stop()
        pump(app, 0.5)
        assert fake.sent[-1] == "STOP", fake.sent
        print("3) 急停: OK")

        # 4) 收到 TEL 回传 → 结构化显示
        app._on_line_cb("TEL:BAT:7.6,SPD_L:12.3", Protocol.parse_line("TEL:BAT:7.6,SPD_L:12.3"))
        pump(app, 0.5)
        log = app.log.get("1.0", "end")
        assert "RX< TEL BAT=7.6, SPD_L=12.3" in log, log
        print("4) TEL 回传结构化显示: OK")

        # 5) 断开
        app.on_disconnect()
        pump(app, 0.5)
        assert fake.connected is False
        assert "未连接" in app.status_var.get()
        print("5) 断开流程: OK")

        # 6) 连接失败路径回归(曾出现 NameError: exc 被 except 销毁后 lambda 延迟引用)
        from ble_chassis_control.link import LinkError

        async def failing_connect():
            raise LinkError("串口被占用测试")

        fake.connect = failing_connect
        app.on_connect()
        pump(app, 1.0)
        log = app.log.get("1.0", "end")
        assert "串口被占用测试" in log, log
        assert "连接失败" in app.status_var.get(), app.status_var.get()
        assert str(app.btn_conn["state"]) == "normal", "失败后连接按钮应恢复"
        print("6) 连接失败路径(原 NameError bug 回归): OK")

        # 7) 连接成功后按钮状态回归(曾出现:_ready_buttons 把连接按钮错误恢复为可点)
        async def ok_connect():
            fake.connected = True
            await asyncio.sleep(0.05)
            return True

        fake.connect = ok_connect
        app.on_connect()
        pump(app, 1.0)
        assert "已连接" in app.status_var.get(), app.status_var.get()
        assert str(app.btn_conn["state"]) == "disabled", "连接成功后连接按钮应禁用"
        assert str(app.btn_disc["state"]) == "normal", "连接成功后断开按钮应可用"
        print("7) 连接成功后按钮状态: OK")

        # 8) 已连接时重复点击"连接"应被忽略(曾出现:自锁式二次打开串口)
        n_calls = [0]

        async def counting_connect():
            n_calls[0] += 1
            return True

        fake.connect = counting_connect
        app.on_connect()   # 已在连接状态
        pump(app, 0.6)
        assert n_calls[0] == 0, f"已连接时不应再次调用 connect,实际调用 {n_calls[0]} 次"
        log = app.log.get("1.0", "end")
        assert "已连接,忽略重复" in log, log
        print("8) 已连接时重复连接防护: OK")

        print("\nGUI 功能测试(模拟链路)全部通过 ✓")
    finally:
        app.on_close()


if __name__ == "__main__":
    run()