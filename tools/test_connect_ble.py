import asyncio
from bleak import BleakClient

ADDRESS = "7B:B7:05:04:23:A2"

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