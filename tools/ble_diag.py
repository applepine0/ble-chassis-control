"""诊断:JDY-23 连接 + 服务枚举(推荐写法:扫描拿 BLEDevice 对象再连接)。"""
import asyncio
import sys
import traceback

from bleak import BleakClient, BleakScanner

ADDRESS = "7B:B7:05:04:23:A2"


async def main() -> None:
    print("==> 1) 扫描查找设备 ...", flush=True)
    device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
    if device is None:
        print("!! 未找到设备,退出")
        return
    print(f"==> 找到: name={device.name!r} address={device.address}")
    print(f"    details={device.details}", flush=True)

    print("==> 2) 连接(超时 30s)...", flush=True)
    try:
        client = BleakClient(device, timeout=30.0)
        await client.connect()
    except Exception:
        print("!! 连接失败,完整堆栈:")
        traceback.print_exc()
        return

    print(f"==> 已连接: is_connected={client.is_connected}", flush=True)
    try:
        print(f"    MTU = {client.mtu_size}")
    except Exception:
        print("    (MTU 不可用)")

    print("==> 3) 枚举服务/特征/描述符 ...", flush=True)
    try:
        for service in client.services:
            print(f"[服务] {service.uuid}  {service.description}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                print(f"     [特征] {char.uuid}  props={props}")
                for desc in char.descriptors:
                    print(f"         [描述符] {desc.uuid}")
    except Exception:
        traceback.print_exc()

    try:
        await client.disconnect()
        print("==> 已断开")
    except Exception:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)