from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .profiles import BleProfile


@dataclass(frozen=True)
class SeenDevice:
    name: str
    address: str
    rssi: int | None
    service_uuids: tuple[str, ...] = field(default_factory=tuple)


def _matches_device_id(device: BLEDevice, device_id: str) -> bool:
    wanted = device_id.casefold()
    return device.address.casefold() == wanted or (device.name or "").casefold() == wanted


def _looks_like_direct_address(device_id: str) -> bool:
    if device_id.count(":") == 5 and all(
        len(part) == 2 for part in device_id.split(":")
    ):
        return True
    try:
        uuid.UUID(device_id)
    except ValueError:
        return False
    return True


def _matches_profile(adv_data: AdvertisementData, profile: BleProfile) -> bool:
    advertised = {uuid.casefold() for uuid in adv_data.service_uuids}
    return any(uuid.casefold() in advertised for uuid in profile.service_uuids)


async def scan_devices(timeout: float = 10.0) -> list[SeenDevice]:
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices: list[SeenDevice] = []
    for device, adv_data in found.values():
        devices.append(
            SeenDevice(
                name=device.name or adv_data.local_name or "(unknown)",
                address=device.address,
                rssi=getattr(adv_data, "rssi", None),
                service_uuids=tuple(adv_data.service_uuids or ()),
            )
        )
    devices.sort(key=lambda item: item.rssi if item.rssi is not None else -999, reverse=True)
    return devices


async def find_printer(
    *,
    profile: BleProfile,
    device_id: str | None = None,
    timeout: float = 10.0,
) -> BLEDevice | str:
    if device_id and _looks_like_direct_address(device_id):
        return device_id

    def predicate(device: BLEDevice, adv_data: AdvertisementData) -> bool:
        if device_id:
            return _matches_device_id(device, device_id)
        return _matches_profile(adv_data, profile)

    device = await BleakScanner.find_device_by_filter(predicate, timeout=timeout)
    if device is None:
        if device_id:
            raise RuntimeError(f"could not find BLE device {device_id!r}")
        raise RuntimeError("could not auto-discover a supported BLE printer")
    return device


async def inspect_device(
    *,
    profile: BleProfile,
    device_id: str | None,
    timeout: float = 10.0,
) -> list[str]:
    device = await find_printer(profile=profile, device_id=device_id, timeout=timeout)
    if isinstance(device, str):
        lines = [f"device: {device}"]
    else:
        lines = [f"device: {device.name or '(unknown)'} {device.address}"]
    async with BleakClient(device) as client:
        lines.append(f"connected: {client.is_connected}")
        lines.append(f"mtu: {client.mtu_size}")
        services = client.services
        if services is None:
            services = await client.get_services()
        for service in services:
            lines.append(f"service {service.uuid} {service.description}")
            for char in service.characteristics:
                props = ",".join(char.properties)
                lines.append(f"  char {char.uuid} props={props} {char.description}")
    return lines


def _chunk(data: bytes, size: int):
    for index in range(0, len(data), size):
        yield data[index : index + size]


async def send_print_job(
    data: bytes,
    *,
    profile: BleProfile,
    device_id: str | None = None,
    scan_timeout: float = 10.0,
    chunk_delay: float = 0.02,
    ready_timeout: float = 30.0,
    ready_notification: bytes | None = None,
) -> None:
    device = await find_printer(profile=profile, device_id=device_id, timeout=scan_timeout)
    ready = asyncio.Event()

    def on_notify(_sender, payload: bytearray):
        if ready_notification is None or bytes(payload) == ready_notification:
            ready.set()

    async with BleakClient(device) as client:
        services = client.services
        if services is None:
            services = await client.get_services()
        if profile.rx_characteristic_uuid and ready_notification is not None:
            await client.start_notify(profile.rx_characteristic_uuid, on_notify)

        chunk_size = max(20, client.mtu_size - 3)
        for packet in _chunk(data, chunk_size):
            await client.write_gatt_char(
                profile.tx_characteristic_uuid,
                packet,
                response=False,
            )
            await asyncio.sleep(chunk_delay)

        if profile.rx_characteristic_uuid and ready_notification is not None:
            try:
                await asyncio.wait_for(ready.wait(), timeout=ready_timeout)
            except asyncio.TimeoutError:
                pass


async def send_packet_sequence(
    packets,
    *,
    profile: BleProfile,
    device_id: str | None = None,
    scan_timeout: float = 10.0,
    packet_delay: float = 0.01,
    response: bool = False,
) -> None:
    device = await find_printer(profile=profile, device_id=device_id, timeout=scan_timeout)

    async with BleakClient(device) as client:
        services = client.services
        if services is None:
            services = await client.get_services()

        chunk_size = max(20, client.mtu_size - 3)
        for packet in packets:
            data = bytes(packet)
            for chunk in _chunk(data, chunk_size):
                await client.write_gatt_char(
                    profile.tx_characteristic_uuid,
                    chunk,
                    response=response,
                )
                await asyncio.sleep(packet_delay)
