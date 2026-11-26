"""GUI 功能级测试:用模拟后端跑通「连接 → 发送 MOT → 收到 TEL 回传 → 日志渲染」。

不接触硬件;验证前端线程模型、队列回传、按钮逻辑与日志渲染整条链路。
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ble_chassis_control import config
from ble_chassis_control.protocol import Protocol  # noqa: E402


class FakeBackend:
    """模拟 BLEBackend 的异步接口。"""

    def __init__(self) -> None:
        self.address = "00:11:22:33:44:55"
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

    async def send_set(self, name: str, value: float, scale: int | None = None) -> None:
        self.sent.append(Protocol.encode_set(name, value, scale=scale).decode().strip())

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def send(self, data: bytes) -> None:
        self.sent.append(data.decode("utf-8").strip())


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
        assert "TX> VEL 0.5 m/s, 90°, -0.5 rad/s" in app.log.get("1.0", "end"), app.log.get(
            "1.0", "end"
        )
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
        app.on_connect()  # 已在连接状态
        pump(app, 0.6)
        assert n_calls[0] == 0, f"已连接时不应再次调用 connect,实际调用 {n_calls[0]} 次"
        log = app.log.get("1.0", "end")
        assert "已连接,忽略重复" in log, log
        print("8) 已连接时重复连接防护: OK")

        # 9) SET 方案 A:物理值按参数缩放成整数,并在界面显示单位/缩放
        app.set_name.set("MAX_SPEED")
        app.set_val.delete(0, "end")
        app.set_val.insert(0, "0.8")
        app._update_set_hint()
        assert "×1000" in app.set_hint_var.get(), app.set_hint_var.get()
        app.on_send_set()
        pump(app, 0.5)
        assert fake.sent[-1] == "SET:MAX_SPEED:800", fake.sent
        log = app.log.get("1.0", "end")
        assert "SET:MAX_SPEED:800" in log, log
        print("9) SET 缩放(GUI 显示 ×1000,线上 800): OK")

        # 10) 运动中断开:必须先等 STOP 发出,再关闭链路
        app.speed_var.set(0.5)
        app.deg_var.set(0.0)
        app.omega_var.set(0.5)
        app.on_motion_start()
        pump(app, 0.4)
        n_before = len(fake.sent)
        app.on_disconnect()
        pump(app, 1.5)
        assert fake.connected is False, "断开后 connected 应为 False"
        assert "STOP" in fake.sent[n_before:], fake.sent[n_before:]
        print("10) 运动中断开:先发 STOP 再断开: OK")

        # 11) 运动中切换链路:旧链路也必须先收到 STOP
        fake.connected = True
        app.on_motion_start()
        pump(app, 0.4)
        n_before = len(fake.sent)
        app.link_kind.set("ble")
        app._rebuild_link()
        pump(app, 1.5)
        assert "STOP" in fake.sent[n_before:], fake.sent[n_before:]
        assert fake.connected is False, "旧链路应先发 STOP 再断开"
        print("11) 运动中断开旧链路:先发 STOP 再切换: OK")

        # 12) BLE/串口上下文切换:BLE 隐藏串口控件,切回恢复并记住端口
        app.link_kind.set("serial")
        app._sync_target_field()
        pump(app, 0.2)
        serial_port = app.addr_var.get().strip()
        app.link_kind.set("ble")
        app._rebuild_link()
        pump(app, 0.2)
        assert app.addr_var.get() == config.DEVICE_ADDRESS
        assert not app.baud_box.winfo_ismapped(), "BLE 模式不应显示波特率"
        assert not app.refresh_btn.winfo_ismapped(), "BLE 模式不应显示刷新口"
        app.link_kind.set("serial")
        app._rebuild_link()
        pump(app, 0.2)
        assert app.baud_box.winfo_ismapped(), "串口模式应恢复波特率"
        assert app.refresh_btn.winfo_ismapped(), "串口模式应恢复刷新口"
        if serial_port:
            assert app.addr_var.get() == serial_port, app.addr_var.get()
        print("12) BLE/串口上下文切换: OK")

        # 13) 掉线/自动重连状态同步到状态栏与按钮
        app._handle_state("串口连接中断")
        assert "中断" in app.status_var.get(), app.status_var.get()
        assert str(app.btn_conn["state"]) == "disabled"
        assert str(app.btn_disc["state"]) == "normal"
        app._handle_state("5 秒后自动重连串口 ...")
        assert "重连" in app.status_var.get(), app.status_var.get()
        app._handle_state("串口自动重连成功")
        assert "已连接" in app.status_var.get(), app.status_var.get()
        print("13) 掉线/自动重连状态同步: OK")

        # 14) 日志行数上限
        import ble_chassis_control.frontend as fe

        old_max = fe.MAX_LOG_LINES
        fe.MAX_LOG_LINES = 50
        try:
            for i in range(120):
                app.log_sys(f"log-{i}")
            lines = int(app.log.index("end-1c").split(".")[0])
            assert lines <= 50, lines
        finally:
            fe.MAX_LOG_LINES = old_max
        print("14) 日志行数上限: OK")

        # 15) 放大曲线:独立窗口继承主图数据,可继续推送,关闭后释放
        app.chart.push((1.0, 2.0, 3.0, 4.0))
        app.on_expand_chart()
        pump(app, 0.3)
        assert app.chart_window is not None
        assert len(app.chart_window.chart._series[0]) >= 1
        app.chart_window.push((5.0, 6.0, 7.0, 8.0))
        pump(app, 0.2)
        assert app.chart_window.chart._series[0][-1] == 5.0
        app.chart_window.close()
        pump(app, 0.2)
        assert app.chart_window is None
        print("15) 放大曲线独立窗口: OK")

        # 16) 字体设置:应用后命名字体与日志控件同步
        import tkinter.font as tkfont

        old_save = fe.save_font_settings
        fe.save_font_settings = lambda _s: None
        try:
            app.on_font_settings()
            pump(app, 0.2)
            assert app.settings_window is not None
            app.settings_window.ui_size_var.set(12)
            app.settings_window._apply()
            pump(app, 0.2)
            assert app.font_settings.ui_size == 12
            assert tkfont.nametofont("TkDefaultFont").cget("size") == 12
            # 命名字体必须真实存在(不能被 Python GC 删掉)
            assert tkfont.nametofont("AppLegendFont").cget("size") == 12
            if "SunValleyBodyFont" in tkfont.names():
                assert tkfont.nametofont("SunValleyBodyFont").cget("size") == 9
            app.settings_window.close()
            pump(app, 0.2)
            assert app.settings_window is None
        finally:
            fe.save_font_settings = old_save
        print("16) 字体设置窗口/应用: OK")

        # 17) 曲线暂停 / CSV / 主题切换
        from pathlib import Path

        from ble_chassis_control.ui.theme import FontSettings

        csv_path = Path("tmp_test_chart.csv")
        try:
            app.chart.clear()
            app.chart.push((1.0, 2.0, 3.0, 4.0))
            app.chart.push((5.0, 6.0, 7.0, 8.0))
            assert app.chart.export_csv(str(csv_path)) == 2
            assert "time_s" in csv_path.read_text(encoding="utf-8-sig")

            before = app.chart.data.revision
            assert app.chart.toggle_pause() is True
            app.chart.push((9.0, 9.0, 9.0, 9.0))
            # 暂停只冻结当前视图,共享数据源继续更新
            assert app.chart.data.revision == before + 1
            assert app.chart.toggle_pause() is False

            old_save = fe.save_font_settings
            fe.save_font_settings = lambda _s: None
            try:
                app._apply_font_settings(FontSettings(theme="dark"))
                assert app.palette.name == "dark"
            finally:
                app._apply_font_settings(FontSettings(theme="light"))
                fe.save_font_settings = old_save
        finally:
            csv_path.unlink(missing_ok=True)
        print("17) 曲线暂停/CSV/主题切换: OK")

        # 18) 辅助执行器:三个模块独立下发
        fake.connected = True
        app.backend = fake
        app.lift_var.set(125)
        app.aux_var.set(45.0)
        app.cyl_var.set("伸出")
        app.on_send_lift()
        app.on_send_aux()
        app.on_send_cyl()
        pump(app, 0.4)
        assert "LIFT:125" in fake.sent, fake.sent
        assert "AUX:1:450" in fake.sent, fake.sent
        assert "CYL:1:1" in fake.sent, fake.sent
        app._update_ext_feedback({"LIFT": 125, "AUX1": 450, "CYL1": 1})
        assert "125 mm" in app.ext_status_var.get()
        assert "45.0°" in app.ext_status_var.get()
        assert "伸出" in app.ext_status_var.get()
        print("18) 辅助执行器独立下发/反馈: OK")

        print("\nGUI 功能测试(模拟链路)全部通过 ✓")
    finally:
        app.on_close()


if __name__ == "__main__":
    run()
