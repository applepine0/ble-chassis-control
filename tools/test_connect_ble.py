import asyncio
import os

from bleak import BleakClient

ADDRESS = os.getenv("CHASSIS_BLE_ADDRESS", "").strip().upper()

if not ADDRESS:
    raise SystemExit("请设置 CHASSIS_BLE_ADDRESS 环境变量或传入模块 MAC")


async def main():
    print(f"连接 {ADDRESS} ...")
    async with BleakClient(ADDRESS) as client:
        print(f"已连接: {client.is_connected}\n")
        for service in client.services:
            print(f"[服务] {service.uuid}")
            print(f"  描述: {service.description}")
            for char in service.characteristics:
                print(f"  [特征] {char.uuid}")
                print(f"    属性: {char.properties}")
                print(f"    描述: {char.description}")
            print()


asyncio.run(main())
