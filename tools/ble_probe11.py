"""探针 11: GATT 冲刷 —— 短会话密集重连枚举(8 次 x 8s)。

与之前 40s 长会话重试不同:快速建链-枚举-拆链循环,
用于发现「固件 GATT 偶发可用」的情况。
"""
import asyncio
import traceback
from bleak import BleakClient, BleakScanner

ADDRESS = "7B:B7:05:04:23:A2"


async def main() -> None:
    device = await BleakScanner.find_device_by_address(ADDRESS, timeout=10.0)
    if device is None:
        print("!! 未扫描到模块")
        return
    ok = 0
    for i in range(1, 9):
        c = BleakClient(
            device,
            timeout=8.0,
            winrt={"use_cached_services": False, "address_type": "public"},
        )
        try:
            await c.connect()
            n = len(list(c.services))
            print(f"-- 第 {i} 次: 连接成功, 服务数={n}", flush=True)
            for s in list(c.services):
                print(f"     [服务] {s.uuid}", flush=True)
                for ch in list(s.characteristics)[:4]:
                    print(f"        [特征] {ch.uuid} props={','.join(ch.properties)}", flush=True)
            ok += 1
            await c.disconnect()
            break
        except Exception as exc:
            print(f"-- 第 {i} 次: {type(exc).__name__}", flush=True)
            try:
                c.close()
            except Exception:
                pass
            await asyncio.sleep(0.5)
    print("\n==> 冲刷结果:", "成功 ✓" if ok else "8 次全失败 —— 固件 GATT 稳定不可用")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()