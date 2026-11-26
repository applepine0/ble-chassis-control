"""探针 9: 对照实验 —— 用同一台电脑连接其他 BLE 设备并枚举 GATT。

目的:区分「模块问题」还是「PC 蓝牙栈问题」。
  任一对照设备 GATT 枚举成功  => PC 栈健康,JDY-23 模块 100% 有恙;
  全部设备同样超时            => Realtek BLE 栈系统性问题,模块可能无辜。

用法:
  uv run python tools/ble_probe9.py <MAC1> [<MAC2> ...]
不内置任何设备地址。
"""

import asyncio
import sys
import traceback

from bleak import BleakClient, BleakScanner


async def try_device(label: str, address: str) -> None:
    print(f"\n===== [{label}] {address} =====", flush=True)
    device = await BleakScanner.find_device_by_address(address, timeout=8.0)
    if device is None:
        print("-- 未扫描到,跳过")
        return
    print(f"-- 找到 name={device.name!r}", flush=True)
    c = BleakClient(device, timeout=15.0, winrt={"use_cached_services": False})
    try:
        await c.connect()
    except Exception as exc:
        print(f"-- 连接/GATT 失败: {type(exc).__name__}: {exc}", flush=True)
        return
    try:
        print(f"-- 连接成功! 服务数={len(list(c.services))}", flush=True)
        for s in list(c.services)[:6]:
            print(f"   [服务] {s.uuid} ({s.description})", flush=True)
            for ch in list(s.characteristics)[:5]:
                print(f"      [特征] {ch.uuid} props={','.join(ch.properties)}", flush=True)
        print(f"-- [{label}] 枚举成功 ✓ —— PC 蓝牙栈健康", flush=True)
    except Exception as exc:
        print(f"-- 枚举失败: {type(exc).__name__}: {exc}", flush=True)
    finally:
        try:
            await c.disconnect()
        except Exception:
            pass


async def main() -> None:
    targets = sys.argv[1:]
    if not targets:
        print("用法: uv run python tools/ble_probe9.py <MAC1> [<MAC2> ...]")
        return
    for i, address in enumerate(targets, 1):
        await try_device(f"target-{i}", address.upper())
        await asyncio.sleep(1)
    print("\n==> 对照结论:见上方逐台结果。若对照设备可枚举而 JDY-23 不能,则 PC 栈健康、模块有恙。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()
