"""ble-chassis-control:底盘上位机(串口 HC-05/06 / BLE 双链路)。

注意:此处**不导入 tkinter 依赖**(frontend)。这样在无 GUI 环境(无头服务器、
未装 tkinter 的 Python、CI)下,仍可直接使用 protocol / link / motion 等模块:

    from ble_chassis_control.protocol import Protocol
    from ble_chassis_control.motion import MotionRunner

GUI 入口保留为惰性函数,调用时才导入 frontend。
"""
from __future__ import annotations

__all__ = ["main"]


def main() -> None:
    """启动 GUI 上位机(console_scripts 入口)。"""
    from .frontend import main as _gui_main

    _gui_main()
