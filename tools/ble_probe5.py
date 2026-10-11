"""探针 5: 最后两个旁路尝试 —— ① 读 Windows 已有 GATT 缓存(免射频交互)② 单次 90s 超长等待。"""
import asyncio
import traceback
from bleak import BleakClient, BleakScanner

ADDRESS = "7B:B7:05:04:23:A2"


async def try_once(label: str, timeout: float, winrt: dict) -> None:
    print(f"\n===== [{label}] timeout={timeout}s winrt={winrt} =====", flush=True)
    device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
    if device is None:
        print("!! 未扫描到设备")
        return
    c = BleakClient(device, timeout=timeout, winrt=winrt)
    try:
        await c.connect()
    except Exception as exc:
        print(f"-- 失败: {type(exc).__name__}: {exc}", flush=True)
        return
    try:
        print(f"-- 连接成功! 服务数={len(c.services)}", flush=True)
        for s in c.services:
            print(f"   [服务] {s.uuid}", flush=True)
            for ch in s.characteristics:
                print(f"      [特征] {ch.uuid} props={','.join(ch.properties)}", flush=True)
        print(f"-- [{label}] 成功 ✓", flush=True)
    except Exception as exc:
        print(f"-- 枚举失败: {type(exc).__name__}: {exc}", flush=True)
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass


async def main() -> None:
    await try_once("I: 读缓存(CACHED)", 30.0, {"use_cached_services": True, "address_type": "public"})
    await asyncio.sleep(2)
    await try_once("J: 超长等待 90s", 90.0, {"use_cached_services": False, "address_type": "public"})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()