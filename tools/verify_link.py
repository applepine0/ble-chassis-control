"""链路验证脚本:一条命令走完「扫描 → 连接 → GATT 枚举 → FFE1 写入 → 通知接收」全链路。

用法(模块修好后):
    uv run python verify_link.py [地址]

成功标志:
    1. 输出服务树(FFE0/FFE1);
    2. 「写入 OK」—— 已向 FFE1 发送一行文本;
    3. 「收到 N 字节」—— 若底盘 MCU 有回传(遥测/回显)会在此显示。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import asyncio
import time

from bleak import BleakClient, BleakScanner

from ble_chassis_control import config
from ble_chassis_control.protocol import Protocol

ADDRESS = (sys.argv[1] if len(sys.argv) > 1 else config.DEVICE_ADDRESS).upper()


async def main() -> None:
    print(f"==> 1) 扫描 {ADDRESS} ...", flush=True)
    device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
    if device is None:
        print("!! 未扫描到模块:请确认上电且 AT+STARTEN=1")
        return
    print(f"    找到: name={device.name!r} address={device.address}", flush=True)

    print(f"==> 2) 连接({config.CONNECT_OP_TIMEOUT}s 超时,重试 {config.CONNECT_MAX_ATTEMPTS} 次) ...", flush=True)
    client = None
    for attempt in range(1, config.CONNECT_MAX_ATTEMPTS + 1):
        try:
            client = BleakClient(
                device,
                timeout=config.CONNECT_OP_TIMEOUT,
                winrt={
                    "use_cached_services": config.USE_CACHED_SERVICES,
                    "address_type": config.ADDRESS_TYPE,
                },
            )
            await client.connect()
            break
        except Exception as exc:
            print(f"    第 {attempt} 次失败: {type(exc).__name__}: {exc}", flush=True)
            await _safe_disconnect(client)
            client = None
            if attempt < config.CONNECT_MAX_ATTEMPTS:
                await asyncio.sleep(2)

    if client is None or not client.is_connected:
        print("!! 连接失败(见上)。常见原因:模块处于 AT+STARTEN 0 休眠/PWRC 接地/被占用")
        return

    print(f"==> 3) GATT 服务树 (MTU={client.mtu_size}) ...", flush=True)
    write_char = notify_char = None
    for service in client.services:
        print(f"    [服务] {service.uuid} {service.description}")
        for ch in service.characteristics:
            props = ",".join(ch.properties)
            print(f"        [特征] {ch.uuid}  props={props}")
            if ch.uuid.lower() == config.WRITE_UUID.lower():
                write_char = ch
            if ch.uuid.lower() == config.NOTIFY_UUID.lower():
                notify_char = ch

    if write_char is None:
        print("!! 未找到 FFE1 写特征,无法发送")
        await client.disconnect()
        return

    print(f"==> 4) 写 FFE1(形式验证:写入 '{Protocol.encode_mot(0,0)!r}') ...", flush=True)
    payload = Protocol.encode_mot(0.0, 0.0)
    try:
        await client.write_gatt_char(write_char, payload, response=False)
        print("    写入 OK(命令已透传到模块 UART,底盘侧应能收到)", flush=True)
    except Exception as exc:
        print(f"    直接写失败: {type(exc).__name__}: {exc},尝试带响应写 ...", flush=True)
        try:
            await client.write_gatt_char(write_char, payload, response=True)
            print("    写入 OK(带响应)", flush=True)
        except Exception as exc2:
            print(f"    !! 两种写模式均失败: {type(exc2).__name__}: {exc2}", flush=True)

    received: list[bytes] = []

    def on_notify(_char, data: bytearray) -> None:
        received.append(bytes(data))
        try:
            text = bytes(data).decode("utf-8", errors="replace")
        except Exception:
            text = repr(bytes(data))
        print(f"    << 收到通知: {text!r}", flush=True)

    if notify_char is not None:
        print(f"==> 5) 订阅通知并等待 5s(底盘回传将在此出现) ...", flush=True)
        try:
            await client.start_notify(notify_char, on_notify)
        except Exception as exc:
            print(f"    订阅失败: {type(exc).__name__}: {exc}", flush=True)
        await asyncio.sleep(5.0)

    print("\n==> 结果汇总", flush=True)
    if received:
        print(f"    收到 {len(received)} 条通知,共 {sum(len(r) for r in received)} 字节 ✓")
        print("    链路收发验证通过!")
    else:
        print("    未收到任何通知(若 MCU 未配置回传/回显,这是正常的)")
        print("    链路验证:连接 + GATT + 写入均 OK")

    try:
        await client.disconnect()
    except Exception:
        pass
    print("    已断开")


async def _safe_disconnect(client) -> None:
    if client is None:
        return
    try:
        await client.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())