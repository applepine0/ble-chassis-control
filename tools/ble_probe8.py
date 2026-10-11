"""探针 8: 快速连发连接 —— 6 次 x 15s,间隔最小化。

思路:模块固件在 CONNECTABLE 广播下,连接事件本身可唤醒 nRF 协议栈;
慢速串行重试(40s 超时+冷却)可能每次都错过唤醒窗口,密集连发则不同。
成功即:输出服务树 → 写 FFE1(MOT:0,0)→ 订阅通知等 3s。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import asyncio
import traceback
from bleak import BleakClient, BleakScanner

from ble_chassis_control import config
from ble_chassis_control.protocol import Protocol

ADDRESS = config.DEVICE_ADDRESS


async def main() -> None:
    device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
    if device is None:
        print("!! 未扫描到模块")
        return
    print(f"==> 快速连发 6 次 x 15s ...", flush=True)
    for i in range(1, 7):
        client = BleakClient(
            device,
            timeout=15.0,
            winrt={"use_cached_services": False, "address_type": "public"},
        )
        try:
            await client.connect()
            print(f"-- 第 {i} 次连接成功! MTU={client.mtu_size}", flush=True)
            write_char = None
            for svc in client.services:
                print(f"   [服务] {svc.uuid} {svc.description}", flush=True)
                for ch in svc.characteristics:
                    print(f"      [特征] {ch.uuid} props={','.join(ch.properties)}", flush=True)
                    if ch.uuid.lower() == config.WRITE_UUID.lower():
                        write_char = ch
            if write_char is not None:
                payload = Protocol.encode_mot(0.0, 0.0)
                try:
                    await client.write_gatt_char(write_char, payload, response=False)
                    print("-- 写入 FFE1 OK(MOT:0,0 已透传到 UART)", flush=True)
                except Exception as exc:
                    print(f"-- 写入失败: {type(exc).__name__}: {exc}", flush=True)
            else:
                print("-- 未找到 FFE1(服务树异常?)", flush=True)

            async def on_notify(_c, data: bytearray) -> None:
                print(f"   << 通知: {bytes(data)!r}", flush=True)

            try:
                await client.start_notify(write_char, on_notify)
                await asyncio.sleep(3.0)
            except Exception as exc:
                print(f"-- 订阅通知失败: {type(exc).__name__}: {exc}", flush=True)
            await client.disconnect()
            print("-- 完成,断开 ✓", flush=True)
            return
        except Exception as exc:
            print(f"-- 第 {i} 次失败: {type(exc).__name__}: {exc}", flush=True)
            try:
                client.close()
            except Exception:
                pass
            # 不等待,立即下一发
    print("\n!! 6 次快速连发全部失败 —— 连接事件未唤醒固件")
    print("   结论:模块固件连接唤醒未生效,按 README 执行 AT+STARTEN1 或排查占用")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()