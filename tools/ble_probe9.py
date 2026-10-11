"""探针 9: 对照实验 —— 用同一台电脑连接其他 BLE 设备并枚举 GATT。

目的:区分「模块问题」还是「PC 蓝牙栈问题」。
  任一对照设备 GATT 枚举成功  => PC 栈健康,JDY-23 模块 100% 有恙;
  全部设备同样超时            => Realtek BLE 栈系统性问题,模块可能无辜。
"""
import asyncio
import traceback
from bleak import BleakClient, BleakScanner

# 从近期扫描中挑出的可连接设备(可能需配对,仅作定性判断)
TARGETS = [
    ("Bass W04 音箱", "41:BB:01:02:63:AD"),
    ("iKF-T1 耳机", "13:82:D2:56:B3:96"),
    ("DARKRIM SHADOW", "B9:60:72:A3:E6:05"),
]


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
    # 仅聚焦已确证可连接的 DARKRIM SHADOW,并重试 Bass W04
    await try_device("DARKRIM SHADOW(复核)", "B9:60:72:A3:E6:05")
    await asyncio.sleep(2)
    await try_device("Bass W04 音箱(重试)", "41:BB:01:02:63:AD")
    print("\n==> 对照结论:见上方逐台结果。若对照设备可枚举而 JDY-23 不能,则 PC 栈健康、模块有恙。")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()