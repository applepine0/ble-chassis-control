"""串口诊断:排查 HC-05 "只能连一次" / 端口占用 / 端口消失等问题。

用法:
    uv run python diagnose_serial.py            # 全量诊断
    uv run python diagnose_serial.py COM7       # 针对指定口
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ble_chassis_control.backend_serial import available_ports, guess_port


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 62)
    print(" 串口诊断报告")
    print("=" * 62)
    print(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 1) 枚举端口
    print("[1] 当前系统串口:")
    ports = available_ports()
    if not ports:
        print("    !! 未检测到任何串口")
        print("       → USB-TTL 是否插好?HC-05 是否已配对?")
        return
    for dev, desc in ports:
        flag = ""
        if "[蓝牙]" in desc:
            flag = "  <-- 蓝牙 SPP 虚拟口"
        elif "CH340" in desc.upper() or "USB-SERIAL" in desc.upper():
            flag = "  <-- USB-TTL 有线口"
        print(f"    {dev:<10} {desc}{flag}")

    print(f"\n    自动猜口结果: {guess_port() or '(无法确定)'}")
    if target:
        print(f"    指定目标口:   {target}")

    # 2) 逐个尝试打开(只测能否打开,不读取)
    print("\n[2] 打开测试(检查是否被占用):")
    try:
        import serial
    except ImportError:
        print("    !! 未安装 pyserial")
        return

    for dev, _desc in ports:
        if target and dev.upper() != target.upper():
            continue
        for baud in (9600, 115200):
            try:
                s = serial.Serial(dev, baud, timeout=0.1)
                s.close()
                print(f"    {dev} @ {baud:<7} 打开成功 ✓")
                break
            except PermissionError:
                print(f"    {dev} @ {baud:<7} ✗ 被占用(拒绝访问)")
                print("        → 关闭 VOFA+/串口助手/Arduino IDE")
                print("        → 或上次程序异常退出未释放,等 5~10 秒重试")
                break
            except Exception as exc:
                print(f"    {dev} @ {baud:<7} ✗ {type(exc).__name__}: {exc}")
                break

    # 3) 蓝牙 SPP 专项提示
    bt_ports = [d for d, s in ports if "[蓝牙]" in s]
    print("\n[3] 蓝牙 SPP 专项:")
    if bt_ports:
        print(f"    发现蓝牙虚拟口: {', '.join(bt_ports)}")
        print("    若只能连一次,依次尝试:")
        print("      a) HC-05 断电 5 秒 → 重新上电")
        print("      b) 设备管理器 → 端口 → 卸载该蓝牙串口 → 扫描硬件改动")
        print("      c) 蓝牙设置 → 删除 HC-05 配对 → 重新配对(PIN 1234)")
        print("      d) 蓝牙适配器属性 → 电源管理 → 取消'允许关闭此设备以省电'")
    else:
        print("    未发现蓝牙 SPP 口")
        print("    → HC-05 尚未在 Windows 蓝牙中配对成功")
        print("    → 设置 → 蓝牙和其他设备 → 添加设备 → 蓝牙 → HC-05 → PIN 1234/0000")
        print("    → 配对成功后端口列表才会出现'标准串行 over 蓝牙链接'")

    print("\n" + "=" * 62)
    print(" 诊断完成")
    print("=" * 62)


if __name__ == "__main__":
    main()