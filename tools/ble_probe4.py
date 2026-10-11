"""探针 4: 定向拉取 GATT 服务(services=[FFE0]),绕过全量枚举挂起问题。"""
import asyncio
import traceback
from bleak import BleakClient, BleakScanner

ADDRESS = "7B:B7:05:04:23:A2"
FFE0 = "0000ffe0-0000-1000-8000-00805f9b34fb"
FEE7 = "0000fee7-0000-1000-8000-00805f9b34fb"


async def try_once(label: str, services: list[str] | None, winrt: dict) -> None:
    print(f"\n===== [{label}] services={services} winrt={winrt} =====", flush=True)
    device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
    if device is None:
        print("!! 未扫描到设备")
        return
    c = BleakClient(device, timeout=25.0, services=services, winrt=winrt)
    try:
        await c.connect()
    except Exception as exc:
        print(f"-- 连接失败: {type(exc).__name__}: {exc}", flush=True)
        await _safe_disconnect(c)
        return
    try:
        print(f"-- 连接成功! 服务数={len(c.services)}", flush=True)
        for s in c.services:
            print(f"   [服务] {s.uuid} {s.description}", flush=True)
            for ch in s.characteristics:
                print(f"      [特征] {ch.uuid} props={','.join(ch.properties)}", flush=True)
        print(f"-- [{label}] 成功 ✓", flush=True)
    except Exception as exc:
        print(f"-- 枚举失败: {type(exc).__name__}: {exc}", flush=True)
    finally:
        await _safe_disconnect(c)


async def _safe_disconnect(c: BleakClient) -> None:
    try:
        await c.disconnect()
    except Exception:
        pass


async def main() -> None:
    base = {"use_cached_services": False, "address_type": "public"}
    await try_once("F: 定向 FFE0", [FFE0], base)
    await asyncio.sleep(2)
    await try_once("G: 定向 FEE7", [FEE7], base)
    # 对照组:定向 FFE0 + 默认缓存路径
    await asyncio.sleep(2)
    await try_once("H: 定向 FFE0 + 默认缓存", [FFE0], {"address_type": "public"})


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()