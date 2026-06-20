from __future__ import annotations

import argparse
import asyncio
import contextlib
import string
import uuid

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData


def _is_direct_address(value: str) -> bool:
    if value.count(":") == 5 and all(len(part) == 2 for part in value.split(":")):
        return True
    with contextlib.suppress(ValueError):
        uuid.UUID(value)
        return True
    return False


def _matches(device: BLEDevice, adv_data: AdvertisementData, target: str) -> bool:
    wanted = target.casefold()
    names = {
        name.casefold()
        for name in (device.name, adv_data.local_name)
        if name
    }
    return device.address.casefold() == wanted or wanted in names


def _printable(data: bytes) -> str | None:
    if not data:
        return ""
    allowed = set(string.printable.encode("ascii"))
    if all(byte in allowed and byte not in b"\r\n\t\x0b\x0c" for byte in data):
        return data.decode("ascii", errors="replace")
    return None


def _format_bytes(data: bytes, *, limit: int = 80) -> str:
    shown = data[:limit]
    suffix = "" if len(data) <= limit else f"...(+{len(data) - limit} bytes)"
    hexed = shown.hex(" ")
    text = _printable(shown)
    if text is None:
        return f"hex={hexed}{suffix}"
    return f"hex={hexed}{suffix} ascii={text!r}"


async def _find_target(target: str, *, timeout: float) -> BLEDevice | str:
    if _is_direct_address(target):
        return target

    def predicate(device: BLEDevice, adv_data: AdvertisementData) -> bool:
        return _matches(device, adv_data, target)

    device = await BleakScanner.find_device_by_filter(predicate, timeout=timeout)
    if device is None:
        raise RuntimeError(f"could not find BLE device {target!r}")
    return device


async def inspect_gatt(
    target: str,
    *,
    timeout: float,
    read_values: bool,
) -> None:
    print(f"target: {target}")
    device = await _find_target(target, timeout=timeout)
    if isinstance(device, str):
        print(f"connecting: {device}")
    else:
        print(f"connecting: {device.name or '(unknown)'} {device.address}")

    async with BleakClient(device, timeout=timeout) as client:
        print(f"connected: {client.is_connected}")
        print(f"mtu: {client.mtu_size}")

        services = client.services
        if services is None:
            services = await client.get_services()

        for service in services:
            print(f"service {service.uuid} {service.description}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                max_write = getattr(char, "max_write_without_response_size", None)
                extra = f" max_write_without_response={max_write}" if max_write else ""
                print(f"  char {char.uuid} props={props}{extra} {char.description}")

                if read_values and "read" in char.properties:
                    try:
                        value = await client.read_gatt_char(char.uuid)
                    except Exception as exc:  # BLE stacks can fail reads per characteristic.
                        print(f"    read_error: {type(exc).__name__}: {exc}")
                    else:
                        print(f"    value: {_format_bytes(bytes(value))}")

                for desc in char.descriptors:
                    print(f"    desc {desc.uuid} handle={desc.handle} {desc.description}")
                    if read_values:
                        try:
                            value = await client.read_gatt_descriptor(desc.handle)
                        except Exception as exc:
                            print(f"      read_error: {type(exc).__name__}: {exc}")
                        else:
                            print(f"      value: {_format_bytes(bytes(value))}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect an arbitrary BLE device's GATT services directly.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default="DL-T01",
        help="BLE name or address. Default: DL-T01",
    )
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument(
        "--no-read",
        action="store_false",
        dest="read_values",
        help="Only list services/characteristics; do not read readable values.",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(
        inspect_gatt(
            args.target,
            timeout=args.timeout,
            read_values=args.read_values,
        )
    )


if __name__ == "__main__":
    main()
