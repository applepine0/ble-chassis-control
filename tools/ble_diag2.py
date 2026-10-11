"""诊断 v2: 自动尝试多种连接参数组合,定位 GATT 服务枚举超时问题。

顺序:
  A. use_cached_services=False            (强制 UNCACHED 读取 GATT)
  B. address_type="random"                (地址 0x7B LSB=1 => 随机静态地址)
  C. use_cached_services=False + random
  D. address_type="public"                (对照组)
  E. 默认参数 + pair=True                 (配对尝试)
"""
import asyncio
import traceback

from bleak import BleakClient, BleakScanner

ADDRESS = "7B:B7:05:04:23:A2"
PER_ATTEMPT_TIMEOUT = 25.0
CONFIGS = {
    "A: use_cached_services=False": dict(winrt={"use_cached_services": False}),
    "B: address_type=random": dict(winrt={"address_type": "random"}),
    "C: uncached+random": dict(
        winrt={"use_cached_services": False, "address_type": "random"}
    ),
    "D: address_type=public": dict(winrt={"address_type": "public"}),
    "E: pair=True 默认缓存": dict(pair=True),
}


async def try_config(name: str, device, extra: dict) -> bool:
    print(f"\n===== 尝试 [{name}] =====", flush=True)
    client = BleakClient(device, timeout=PER_ATTEMPT_TIMEOUT, **extra)
    try:
        await client.connect()
    except Exception as exc:
        print(f"-- [{name}] 失败: {type(exc).__name__}: {exc}", flush=True)
        try:
            await client.disconnect()
        except Exception:
            pass
        return False

    try:
        print(f"-- [{name}] 连接成功, 枚举服务 ...", flush=True)
        for service in client.services:
            print(f"   [服务] {service.uuid}  {service.description}")
            for char in service.characteristics:
                print(f"      [特征] {char.uuid}  props={','.join(char.properties)}")
                for desc in char.descriptors:
                    print(f"         [描述符] {desc.uuid}")
        print(f"-- [{name}] 成功 ✓", flush=True)
        return True
    except Exception as exc:
        print(f"-- [{name}] 枚举失败: {type(exc).__name__}: {exc}", flush=True)
        return False
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def main() -> None:
    print("==> 扫描查找设备 ...", flush=True)
    device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
    if device is None:
        print("!! 未找到设备")
        return
    print(f"==> 找到 {device.address} (name={device.name!r})", flush=True)

    for i, (name, extra) in enumerate(CONFIGS.items(), 1):
        print(f"\n--- 第 {i}/{len(CONFIGS)} 组 ---", flush=True)
        ok = await try_config(name, device, extra)
        if ok:
            print(f"\n🎉 配置 [{name}] 可以连通并读取 GATT!")
            return
        await asyncio.sleep(2.0)  # 让蓝牙栈冷却

    print("\n!! 所有配置均失败。请按 README 排查列表操作(见诊断结论)。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()