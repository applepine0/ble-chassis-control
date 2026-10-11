"""探针 6: 裸 WinRT 调用 —— 不经过 Bleak 的 GattSession / maintain_connection。

直接 BluetoothLEDevice.GetGattServices*Async,验证 GATT 枚举挂起的层级:
若裸调用成功 => Bleak 会话层问题,可研发绕行方案;
若同样挂起   => 模块侧确证(证据链闭环)。
"""
import asyncio
import traceback
from uuid import UUID

from bleak.backends.winrt.client import FutureLike, _address_to_int
from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice

ADDRESS = "7B:B7:05:04:23:A2"
FFE0 = "0000ffe0-0000-1000-8000-00805f9b34fb"


async def enumerate_once(label: str, op_fn) -> None:
    print(f"\n== {label} ==", flush=True)
    result = None
    try:
        async with asyncio.timeout(25):
            result = await FutureLike(op_fn())
        print(f"  status={int(result.status)} ({result.status})")
        svcs = [s for s in result.services]
        print(f"  services 数量 = {len(svcs)}")
        for s in svcs:
            print(f"    - {s.uuid}  handle={s.attribute_handle}")
        if svcs:
            svc = svcs[0]
            async with asyncio.timeout(25):
                cresult = await FutureLike(svc.get_characteristics_async())
            print(f"  特征 status={int(cresult.status)}, 数量={len([c for c in cresult.characteristics])}")
            for c in cresult.characteristics:
                print(f"    - {c.uuid} props={c.characteristic_properties} handle={c.attribute_handle}")
    except asyncio.TimeoutError:
        print(f"  !! 25s 超时 —— {label} 挂起", flush=True)
    except Exception as exc:
        print(f"  !! 异常 {type(exc).__name__}: {exc}", flush=True)


async def main() -> None:
    addr = _address_to_int(ADDRESS)
    print(f"addr_int = 0x{addr:X}")
    dev = None
    try:
        dev = await FutureLike(BluetoothLEDevice.from_bluetooth_address_async(addr))
        if dev is None:
            print("!! from_bluetooth_address_async 返回 None")
            return
        print("device_id:", dev.device_id)
        try:
            print("connection_status:", dev.connection_status)
        except Exception as exc:
            print(f"(connection_status 读取失败: {type(exc).__name__}: {exc})")

        await enumerate_once(
            "A. 裸 GetGattServicesAsync(默认)",
            lambda: dev.get_gatt_services_async(),
        )
        await enumerate_once(
            "B. 裸 GetGattServicesWithCacheModeAsync(UNCACHED)",
            lambda: dev.get_gatt_services_with_cache_mode_async(BluetoothCacheMode.UNCACHED),
        )
        await enumerate_once(
            "C. 裸 GetGattServicesForUuidAsync(FFE0, UNCACHED)",
            lambda: dev.get_gatt_services_for_uuid_with_cache_mode_async(
                UUID(FFE0), BluetoothCacheMode.UNCACHED
            ),
        )
    finally:
        if dev is not None:
            try:
                dev.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()