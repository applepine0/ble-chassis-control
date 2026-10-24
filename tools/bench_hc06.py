"""HC-06 链路基准:实测连接稳定性 + 控制频率上限(不依赖 MCU 回显)。

阶段:
  A 低频 20Hz ×15s:基础稳定性
  B 高频 200Hz×10s:指令频率上限
  C 大包 200B×10s :带宽探(摸串口 9600 瓶颈)

注:写入"成功"指 Windows 协议栈已接收;吞吐以写入计(HC-06 未接 MCU
时数据到 UART 即丢弃,不影响串口侧可吸收速率评估)。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import asyncio

from ble_chassis_control.backend_serial import SerialBackend

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM15"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 9600


async def phase(link: SerialBackend, label: str, duration: float, payload: bytes, interval: float) -> None:
    ok = fail = 0
    t0 = time.perf_counter()
    end = t0 + duration
    sent = 0
    while time.perf_counter() < end:
        try:
            await link.send(payload)
            ok += 1
        except Exception:
            fail += 1
        sent += len(payload)
        await asyncio.sleep(interval)
    dt = time.perf_counter() - t0
    print(
        f"  {label:<22} 成功={ok:>5} 失败={fail:>4} "
        f"速率={ok/dt:7.1f} 条/s  吞吐={sent/dt/1024:6.2f} KB/s"
    )


async def main() -> None:
    print(f"==> 打开 {PORT}@{BAUD} ...")
    link = SerialBackend(PORT, BAUD, on_state=lambda t: print(f"  [状态]{t}", flush=True))
    try:
        await link.connect()
    except Exception as exc:
        print(f"!! 打开失败:{exc}")
        return
    print(f"==> 已连接: {link.target}\n")

    mot = b"MOT:25,-25\n"          # 12 字节典型指令
    big = b"X" * 200 + b"\n"       # 200 字节大包

    try:
        await phase(link, "A 低频 20Hz(15s)", 15.0, mot, 0.050)
        await phase(link, "B 高频 200Hz(10s)", 10.0, mot, 0.005)
        await phase(link, "C 大包200B(10s)", 10.0, big, 0.050)
    except KeyboardInterrupt:
        print("\n  中断")

    # 稳定驻留检查
    print("\n==> 额外:掉线驻留检查(8s,零数据)...")
    drops = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 8.0:
        if not link.is_connected:
            drops += 1
        await asyncio.sleep(0.5)
    print(f"  {'无掉线 ✓' if drops == 0 else f'检测到 {drops} 次 is_connected=False'}")

    await link.disconnect()
    print(f"\n==> 基准完成(断开链路)")


if __name__ == "__main__":
    asyncio.run(main())