"""探针 10: 热连接 —— 捕获广播包的瞬间立即发起连接(不等完整扫描)。

此前流程均为 find_device_by_address(8-10s) 后再连接;
若模块固件广播-睡眠窗口切换频繁(对 AT+STARTEN 1 不敏感时也可能),热连接可命中唤醒窗口。
成功即:枚举 GATT → 写 FFE1(MOT:0,0)→ 订阅通知等 3s。
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


async def attempt(label: str) -> None:
    print(f"\n== [{label}] 等待广播包(最长 15s)...", flush=True)
    got = asyncio.Event()
    holder = {}

    def on_detect(dev, _adv) -> None:
        if dev.address.upper() == ADDRESS and not got.is_set():
            holder["dev"] = dev
            got.set()

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    try:
        try:
            await asyncio.wait_for(got.wait(), timeout=15.0)
        except asyncio.TimeoutError:
            print("!! 15s 内未捕获广播包")
            return
        dev = holder["dev"]
        print(f"== 捕获!立即连接 (name={dev.name!r}) ...", flush=True)
        client = BleakClient(
            dev,
            timeout=20.0,
            winrt={"use_cached_services": False, "address_type": "public"},
        )
        try:
            await client.connect()
        except Exception as exc:
            print(f"-- 连接失败: {type(exc).__name__}: {exc}", flush=True)
            try:
                client.close()
            except Exception:
                pass
            return
        print(f"-- 连接成功! MTU={client.mtu_size}", flush=True)
        write_char = None
        for svc in client.services:
            print(f"   [服务] {svc.uuid} {svc.description}", flush=True)
            for ch in svc.characteristics:
                print(f"      [特征] {ch.uuid} props={','.join(ch.properties)}", flush=True)
                if ch.uuid.lower() == config.WRITE_UUID.lower():
                    write_char = ch
        if write_char is not None:
            for response in (False, True):
                try:
                    await client.write_gatt_char(
                        write_char, Protocol.encode_mot(0.0, 0.0), response=response
                    )
                    print(f"-- 写入 FFE1 OK (response={response})", flush=True)
                    break
                except Exception as exc:
                    print(f"-- 写入 response={response} 失败: {type(exc).__name__}: {exc}", flush=True)
        else:
            print("-- 未找到 FFE1", flush=True)

        async def on_notify(_c, data: bytearray) -> None:
            print(f"   << 通知: {bytes(data)!r}", flush=True)

        try:
            await client.start_notify(write_char, on_notify)
            await asyncio.sleep(3.0)
        except Exception as exc:
            print(f"-- 订阅失败: {type(exc).__name__}: {exc}", flush=True)
        await client.disconnect()
        print("-- 完成 ✓", flush=True)
    finally:
        await scanner.stop()


async def main() -> None:
    await attempt("热连接 第 1 发")
    await asyncio.sleep(2)
    await attempt("热连接 第 2 发")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()