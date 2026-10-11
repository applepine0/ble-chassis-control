"""探针 3:
1) 解析广播包 —— 若模块在 ADV 里广播 Service UUID,不连接就能确认协议。
2) 带重试的连接尝试(最多 3 次 x 40s),应对 Windows 偶发首连挂起。
"""
import asyncio
import traceback
from bleak import BleakClient, BleakScanner

ADDRESS = "7B:B7:05:04:23:A2"


async def probe_adv() -> None:
    print("==> 广播包分析 ...", flush=True)
    devices = await BleakScanner.discover(timeout=10.0, return_adv=True)
    hit = None
    for addr, (dev, adv) in devices.items():
        if addr.upper() == ADDRESS.upper():
            hit = (dev, adv)
            break
    if hit is None:
        print("!! 广播中未找到设备")
        return
    dev, adv = hit
    print(f"    name={dev.name!r} rssi={adv.rssi}")
    print(f"    service_uuids={list(adv.service_uuids)}")
    print(f"    manufacturer_data={adv.manufacturer_data}")
    print(f"    service_data={adv.service_data}")
    if adv.platform_data:
        pd = adv.platform_data
        items = pd.items() if isinstance(pd, dict) else enumerate(pd)
        for k, v in items:
            print(f"    platform[{k!r}]: {v!r}")
    # winrt 事件参数里有真正的地址类型
    pd = adv.platform_data
    raw = pd if (hasattr(pd, "adv") or hasattr(pd, "scan")) else (pd[1] if isinstance(pd, tuple) else None)
    ea = getattr(raw, "adv", None) or getattr(raw, "scan", None)
    if ea is not None:
        try:
            print(f"    bluetooth_address_type={ea.bluetooth_address_type}")
        except Exception as e:
            print(f"    (读地址类型失败: {e})")


async def probe_retry_connect() -> None:
    print("\n==> 重试连接(3 次 x 40s)...", flush=True)
    for attempt in range(1, 4):
        print(f"    --- 第 {attempt} 次尝试 ---", flush=True)
        device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
        c = BleakClient(
            device if device else ADDRESS,
            timeout=40.0,
            winrt={"use_cached_services": False},
        )
        try:
            await c.connect()
            print(f"    连接成功! services 数量={len(c.services)}", flush=True)
            for s in c.services:
                print(f"      [服务] {s.uuid} {s.description}")
                for ch in s.characteristics:
                    print(f"        [特征] {ch.uuid} props={','.join(ch.properties)}")
            await c.disconnect()
            print("    断开完成 ✓", flush=True)
            return
        except Exception as exc:
            print(f"    失败: {type(exc).__name__}: {exc}", flush=True)
            try:
                await c.disconnect()
            except Exception:
                pass
            await asyncio.sleep(3.0)
    print("    三次均失败。")


async def main() -> None:
    await probe_adv()
    await probe_retry_connect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()