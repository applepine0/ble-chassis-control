"""HC-05 掉线诊断:针对"显示已连接但几秒后断开"问题。

用法:
    uv run python diagnose_hc05.py COM7          # 指定蓝牙串口
    uv run python diagnose_hc05.py COM7 --listen 30   # 持续监听 30 秒观察掉线

原理:
    * 打开串口后持续读数据,记录任何中断/异常
    * 记录时间线,定位"几秒后断开"发生在哪个阶段
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

try:
    import serial
except ImportError:
    print("!! 未安装 pyserial")
    sys.exit(1)

from ble_chassis_control.backend_serial import available_ports


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python diagnose_hc05.py COM7 [--listen 30]")
        print("\n当前可用串口:")
        for d, s in available_ports():
            print(f"  {d}  {s}")
        return

    port = sys.argv[1]
    duration = 30.0
    if "--listen" in sys.argv:
        try:
            duration = float(sys.argv[sys.argv.index("--listen") + 1])
        except (IndexError, ValueError):
            pass

    print("=" * 62)
    print(f" HC-05 掉线诊断  |  端口 {port}  |  监听 {duration:.0f}s")
    print("=" * 62)

    # 1) 尝试打开
    print(f"\n[T+0.0s] 打开 {port} @ 9600 ...")
    try:
        ser = serial.Serial(port, 9600, timeout=0.3, write_timeout=2.0)
    except PermissionError:
        print("  ✗ 被占用:请关闭 VOFA+/串口助手,并确认没有其他程序连着")
        return
    except Exception as exc:
        print(f"  ✗ {type(exc).__name__}: {exc}")
        print("  → 端口是否存在?设备管理器 → 端口(COM 和 LPT)")
        return

    t0 = time.time()
    print(f"  ✓ 打开成功")
    print(f"  CDC/状态: CD={ser.cd}  CTS={ser.cts}  DSR={ser.dsr}  RI={ser.ri}")
    print("  (若 CTS/DSR 为 False,可能对端未就绪)")

    # 2) 尝试发送探测
    print(f"\n[T+{time.time()-t0:.1f}s] 发送探测字节 'AT\\r\\n' ...")
    try:
        ser.write(b"AT\r\n")
        ser.flush()
        print("  ✓ 写入成功")
    except Exception as exc:
        print(f"  ✗ 写入失败: {type(exc).__name__}: {exc}")

    # 3) 持续监听,记录掉线时刻
    print(f"\n[T+{time.time()-t0:.1f}s] 开始监听(观察是否掉线)...")
    last_data = time.time()
    total = 0
    drop_time = None
    try:
        while time.time() - t0 < duration:
            try:
                n = ser.in_waiting
                if n:
                    data = ser.read(n)
                    total += len(data)
                    last_data = time.time()
                    stamp = time.time() - t0
                    text = data.decode("utf-8", errors="replace")
                    print(f"  [T+{stamp:5.1f}s] << {len(data):3d}B: {text!r}")
            except Exception as exc:
                drop_time = time.time() - t0
                print(f"  ✗ [T+{drop_time:.1f}s] 读取异常: {type(exc).__name__}: {exc}")
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n  手动中断")

    elapsed = time.time() - t0

    # 4) 结果分析
    print("\n" + "=" * 62)
    print(" 结果分析")
    print("=" * 62)
    print(f"  监听时长     : {elapsed:.1f}s")
    print(f"  收到字节数   : {total}")
    print(f"  最后数据时刻 : T+{last_data - t0:.1f}s")

    if drop_time:
        print(f"  ✗ 掉线时刻   : T+{drop_time:.1f}s")
        print("\n  诊断:串口在运行中断开 → 可能是:")
        print("    a) HC-05 供电不足(瞬时电流不够导致重启)")
        print("    b) HC-05 处于 AT 模式(KEY 引脚接了高电平)")
        print("    c) Windows 蓝牙省电挂起设备")
        print("    d) 蓝牙信号弱 / 距离远")
    elif total == 0:
        print("\n  ⚠ 未收到任何数据(但端口保持打开)")
        print("  → 链路已建立但无数据流,检查:")
        print("    a) HC-05 是否处于透传模式(红灯快闪)")
        print("    b) 模块 TXD 是否接到你观察的目标")
        print("    c) 波特率是否匹配(HC-05 默认 9600)")
    else:
        print("\n  ✓ 端口稳定,且收到数据 —— 链路正常")
        print(f"    共 {total} 字节")

    try:
        ser.close()
        print("\n  串口已关闭")
    except Exception:
        pass


if __name__ == "__main__":
    main()