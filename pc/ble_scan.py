#!/usr/bin/env python3
"""BLE scanner + service inspector using bleak.

Usage:
  python pc/ble_scan.py             # scan for 5s and list devices
  python pc/ble_scan.py --timeout 10
  python pc/ble_scan.py --name WioMonitor  # find device by name
  python pc/ble_scan.py --address AA:BB:CC:DD:EE:FF --inspect  # connect and list services

This helps diagnose whether the PC can see the Wio Terminal advertisement and if the UART service/characteristics are present.
"""
import argparse
import asyncio

try:
    from bleak import BleakScanner, BleakClient
except Exception as e:
    print("bleak is not installed or failed to import:", e)
    raise


async def scan(timeout: int):
    devices = await BleakScanner.discover(timeout=timeout)
    return devices


async def inspect(address: str):
    try:
        async with BleakClient(address) as client:
            # is_connected is a property in recent bleak versions
            connected = client.is_connected
            print(f"Connected: {connected}")
            if hasattr(client, 'get_services'):
                services = await client.get_services()
            else:
                # Some bleak versions expose services property after connect
                services = client.services
            print(f"Services for {address}:")
            for svc in services:
                print(f"- Service {svc.uuid} ({getattr(svc, 'description', '')})")
                for ch in svc.characteristics:
                    props = ",".join(ch.properties) if hasattr(ch, 'properties') else ''
                    print(f"  - Char {ch.uuid} props={props}")
    except Exception as e:
        print(f"Connection/inspect failed: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=int, default=5, help="scan timeout seconds")
    parser.add_argument("--name", help="search devices by name containing this string")
    parser.add_argument("--address", help="connect by this address")
    parser.add_argument("--inspect", action="store_true", help="connect and inspect services (requires --address)")
    args = parser.parse_args()

    if args.inspect and not args.address:
        parser.error("--inspect requires --address")

    if args.address and args.inspect:
        asyncio.run(inspect(args.address))
        return

    devices = asyncio.run(scan(args.timeout))
    if not devices:
        print("No BLE devices found")
        return
    print(f"Found {len(devices)} device(s):")
    for d in devices:
        name = d.name or "<unknown>"
        # bleak versions expose RSSI differently
        rssi = None
        if hasattr(d, 'rssi'):
            rssi = d.rssi
        elif hasattr(d, 'metadata') and isinstance(d.metadata, dict):
            rssi = d.metadata.get('rssi')
        print(f"- {d.address} | {name} | RSSI {rssi}")
        if args.name and args.name.lower() in name.lower():
            print("  -> matched by name")


if __name__ == '__main__':
    main()
