"""探针 7: 广播类型诊断 —— 不连接即可判断模块是否可服务连接。

关键指标(winrt 事件参数):
  advertisement_type : CONNECTABLE_UNDIRECTED = 可连接(正常唤醒态)
                      NON_CONNECTABLE_UNDIRECTED = 不可连接(休眠/异常态,常见于 AT+STARTEN 0)
  raw_advertisement_type / flags : 原始广播类型字节与标志
"""
import asyncio
import traceback
from bleak import BleakScanner

ADDRESS = "7B:B7:05:04:23:A2"


async def main() -> None:
    print(f"==> 采集 {ADDRESS} 广播类型(持续 12s,取最新包) ...", flush=True)
    last = {}

    def on_detect(dev, adv) -> None:
        if dev.address.upper() != ADDRESS:
            return
        pd = adv.platform_data
        raw = pd if (hasattr(pd, "scan") or hasattr(pd, "adv")) else (
            pd[1] if isinstance(pd, tuple) else None
        )
        ea = getattr(raw, "scan", None) or getattr(raw, "adv", None)
        info = {"rssi": adv.rssi, "name": dev.name, "uuids": list(adv.service_uuids)}
        attr_name = "advertisement_type"
        if not hasattr(ea, attr_name):
            info["err"] = f"事件参数无 {attr_name} 属性;可用属性: {[a for a in dir(ea) if not a.startswith('_')][:20]}"
        else:
            adv_type = getattr(ea, attr_name)
            info["adv_type"] = adv_type.name if hasattr(adv_type, "name") else str(adv_type)
            if info["adv_type"] == "SCAN_RESPONSE":
                # 扫描响应包:表明模块应答扫描请求(存活信号),但不是主广播
                last.setdefault("saw_scan_response", True)
                return
            advobj = getattr(ea, "advertisement", None)
            flags = getattr(advobj, "flags", None) if advobj else None
            info["flags"] = flags.name if flags is not None and hasattr(flags, "name") else str(flags)
        last.clear()
        last.update(info)

    scanner = BleakScanner(detection_callback=on_detect)
    await scanner.start()
    await asyncio.sleep(12)
    await scanner.stop()

    if not last:
        print("!! 12s 内未捕到该模块的主广播包(仅可能捕到扫描响应,说明模块存活但广播极少)")
        return
    if "err" in last:
        print(f"!! 广播类型信息读取受限: {last['err']}")
    print(f"    name       = {last.get('name')}")
    print(f"    RSSI       = {last.get('rssi')}")
    if "adv_type" in last:
        print(f"    adv_type   = {last.get('adv_type')}")
        print(f"    flags      = {last.get('flags')}")
    else:
        print(f"    (adv_type 未取得:{last.get('err')})")
    print(f"    services   = {last.get('uuids')}")
    if last.get("saw_scan_response"):
        print(f"    (观测到扫描响应包:模块应答扫描请求,存活正常)")
    connectable = "CONNECTABLE" in str(last.get("adv_type", "")).upper()
    print()
    if connectable:
        print("✓ 广播为可连接类型 —— 唤醒态正常;若此时仍连不上,问题在占用/Windows 栈")
    else:
        print("✗ 广播为不可连接类型 —— 模块处于休眠/异常态(典型原因 AT+STARTEN 0 或 PWRC 状态)")
        print("  修复:USB-TTL 发 AT+STARTEN1(断电保存)后重新上电,重跑本探针应变为 CONNECTABLE")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        traceback.print_exc()