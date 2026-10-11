"""探针 12: 同会话内反复枚举(不重开连接)。

此前所有尝试都是"断开会话→重建→枚举";本探针在**同一条已连接会话**上
连续触发 GetGattServicesWithCacheModeAsync(UNCACHED) 4 次,
验证 Windows 是否在某次重试后才拿到服务(部分外设有此行为)。
"""
import asyncio
import traceback

from bleak.backends.winrt.client import FutureLike, _address_to_int
from winrt.windows.devices.bluetooth import BluetoothCacheMode, BluetoothLEDevice

ADDRESS = "7B:B7:05:04:23:A2"


async def main() -> None:
    addr = _address_to_int(ADDRESS)
    dev = await FutureLike(BluetoothLEDevice.from_bluetooth_address_async(addr))
    if dev is None:
        print("!! 设备创建失败")
        return
    try:
        for i in range(1, 5):
            print(f"== 同会话枚举第 {i}/4 (15s) ...", flush=True)
            try:
                async with asyncio.timeout(15):
                    result = await FutureLike(
                        dev.get_gatt_services_with_cache_mode_async(
                            BluetoothCacheMode.UNCACHED
                        )
                    )
                print(f"   status={int(result.status)} services={len(list(result.services))}", flush=True)
                for s in list(result.services)[:6]:
                    print(f"     - {s.uuid}", flush=True)
                if list(result.services):
                    print("== ✓ 同会话重试成功!", flush=True)
                    return
            except asyncio.TimeoutError:
                print("   15s 超时", flush=True)
            except Exception as exc:
                print(f"   异常 {type(exc).__name__}: {exc}", flush=True)
                    # 不重连,下一次循环继续
        print("\n== 4 次同会话枚举全部超时 —— 会话内重试也无效")
    finally:
        try:
            dev.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()