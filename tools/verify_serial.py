"""串口链路验证(HC-05 / USB-TTL):不依赖 GUI,直接验证串口收发。

用法:
    uv run python verify_serial.py                 # 自动选口
    uv run python verify_serial.py COM15 9600      # 指定串口与波特率
    uv run python verify_serial.py COM15 9600 --listen 10   # 只监听 10 秒
    uv run python verify_serial.py COM15 9600 --hold        # 保持连接直到 Ctrl+C
    uv run python verify_serial.py COM15 9600 --hold --send "MOT:25,25"   # 保持并循环发送

注意:连接是"打开即连"—— 脚本退出/断开串口会同时断开蓝牙链路,
导致 HC-05/06 红灯回到闪烁。要持续遥控请用 --hold 或直接开 GUI。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from ble_chassis_control import config
from ble_chassis_control.backend_serial import SerialBackend, available_ports


async def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else config.SERIAL_PORT
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else config.SERIAL_BAUDRATE
    hold = "--hold" in sys.argv
    listen = 8.0
    if "--listen" in sys.argv:
        try:
            listen = float(sys.argv[sys.argv.index("--listen") + 1])
        except (IndexError, ValueError):
            pass

    send_cmd = None
    if "--send" in sys.argv:
        try:
            send_cmd = sys.argv[sys.argv.index("--send") + 1]
        except IndexError:
            pass

    print("==> 可用串口:")
    for d, s in available_ports() or [("(无)", "未检测到串口")]:
        print(f"    {d}  {s}")

    received: list[str] = []

    def on_line(text: str, parsed) -> None:
        received.append(text)
        stamp = time.strftime("%H:%M:%S")
        kind = parsed.kind
        if kind == "tel":
            kv = ", ".join(f"{k}={v:g}" for k, v in parsed.params["values"].items())
            print(f"    [{stamp}] << {text}   (遥测: {kv})", flush=True)
        else:
            print(f"    [{stamp}] << {text}   ({kind})", flush=True)

    link = SerialBackend(
        port, baud,
        on_line=on_line,
        on_state=lambda t: print(f"    [状态] {t}", flush=True),
    )

    print(f"\n==> 打开串口 {port or '(自动)'} @ {baud} ...")
    try:
        await link.connect()
    except Exception as exc:
        print(f"!! 打开失败: {exc}")
        print("\n排查:① 串口是否被 VOFA+/串口助手占用(必须关闭)")
        print("      ② 口名是否正确(设备管理器 → 端口)")
        print("      ③ HC-05/06 是否已配对并出现在端口列表")
        return

    print(f"==> 已打开: {link.target}")
    print(f"    ⚠ 保持连接即可让 HC-05/06 红灯常亮;关闭脚本会断开蓝牙")

    try:
        if send_cmd:
            print(f"==> 循环发送:{send_cmd}")
            while True:
                try:
                    await link.send_text(send_cmd)
                    print(f"    [{time.strftime('%H:%M:%S')}] >> {send_cmd}", flush=True)
                except Exception as exc:
                    print(f"!! 发送失败:{exc}")
                await asyncio.sleep(0.5)
        elif hold:
            print(f"==> 保持连接(红灯应常亮)。Ctrl+C 停止 ...")
            while True:
                await asyncio.sleep(1.0)
        else:
            try:
                await link.send_mot(0.0, 0.0)
                print("    发送 MOT:0,0 OK")
            except Exception as exc:
                print(f"!! 发送失败: {exc}")
            print(f"\n==> 监听 {listen:.0f} 秒(底盘回传 / 模块事件将显示在此)...")
            await asyncio.sleep(listen)
    except KeyboardInterrupt:
        print("\n==> 用户中断")

    print("\n==> 汇总")
    print(f"    收到 {len(received)} 行")
    if received:
        print("    ✓ 串口链路正常(双向)")
    else:
        print("    ⚠ 未收到任何数据。检查:")
        print("      - 模块与 MCU 是否已连接并供电")
        print("      - 波特率是否与模块一致(HC-05/06 默认 9600)")
        print("      - TX/RX 是否交叉、是否共地")

    await link.disconnect()
    print("    已关闭串口(蓝牙链路随之断开,红灯回到闪烁)")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已中断")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n已中断")