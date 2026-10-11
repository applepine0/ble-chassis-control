import asyncio
from bleak import BleakScanner

async def main():
    print("扫描中...")
    devices = await BleakScanner.discover(timeout=8.0, return_adv=True)
    for address, (device, adv) in devices.items():
        print(f"{device.name or '(无名)':<20} {device.address}  RSSI={adv.rssi}")

asyncio.run(main())