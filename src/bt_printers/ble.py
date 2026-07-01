from __future__ import annotations

import asyncio
import sys
import uuid
from dataclasses import dataclass, field

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from .profiles import BleProfile


def _debug(verbose: bool, message: str) -> None:
    if verbose:
        print(f"[ble] {message}", file=sys.stderr)


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


async def scan_devices(timeout: float = 10.0, *, verbose: bool = False) -> list[SeenDevice]:
    _debug(verbose, f"scanning timeout={timeout}s")
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
    _debug(verbose, f"scan complete found={len(devices)}")
    return devices


async def find_printer(
    *,
    profile: BleProfile,
    device_id: str | None = None,
    timeout: float = 10.0,
    verbose: bool = False,
) -> BLEDevice | str:
    if device_id and _looks_like_direct_address(device_id):
        _debug(verbose, f"using direct address {device_id}")
        return device_id

    target = f"name/address {device_id!r}" if device_id else f"profile {profile.name}"
    _debug(verbose, f"finding printer by {target} timeout={timeout}s")

    def predicate(device: BLEDevice, adv_data: AdvertisementData) -> bool:
        if device_id:
            return _matches_device_id(device, device_id)
        return _matches_profile(adv_data, profile)

    device = await BleakScanner.find_device_by_filter(predicate, timeout=timeout)
    if device is None:
        if device_id:
            raise RuntimeError(f"could not find BLE device {device_id!r}")
        raise RuntimeError("could not auto-discover a supported BLE printer")
    _debug(verbose, f"found {device.name or '(unknown)'} {device.address}")
    return device


async def inspect_device(
    *,
    profile: BleProfile,
    device_id: str | None,
    timeout: float = 10.0,
    verbose: bool = False,
) -> list[str]:
    device = await find_printer(
        profile=profile,
        device_id=device_id,
        timeout=timeout,
        verbose=verbose,
    )
    if isinstance(device, str):
        lines = [f"device: {device}"]
    else:
        lines = [f"device: {device.name or '(unknown)'} {device.address}"]
    _debug(verbose, "connecting for GATT inspection")
    async with BleakClient(device) as client:
        lines.append(f"connected: {client.is_connected}")
        lines.append(f"mtu: {client.mtu_size}")
        _debug(verbose, f"connected={client.is_connected} mtu={client.mtu_size}")
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
    verbose: bool = False,
) -> None:
    device = await find_printer(
        profile=profile,
        device_id=device_id,
        timeout=scan_timeout,
        verbose=verbose,
    )
    ready = asyncio.Event()

    def on_notify(_sender, payload: bytearray):
        _debug(verbose, f"notify {bytes(payload).hex(' ')}")
        if ready_notification is None or bytes(payload) == ready_notification:
            ready.set()

    _debug(verbose, "connecting for print job")
    async with BleakClient(device) as client:
        services = client.services
        if services is None:
            services = await client.get_services()
        _debug(verbose, f"connected={client.is_connected} mtu={client.mtu_size}")
        if profile.rx_characteristic_uuid and ready_notification is not None:
            _debug(verbose, f"start notify rx={profile.rx_characteristic_uuid}")
            await client.start_notify(profile.rx_characteristic_uuid, on_notify)

        chunk_size = max(20, client.mtu_size - 3)
        chunks = list(_chunk(data, chunk_size))
        _debug(verbose, f"writing bytes={len(data)} chunks={len(chunks)} chunk_size={chunk_size}")
        for index, packet in enumerate(chunks, start=1):
            _debug(verbose, f"write chunk={index}/{len(chunks)} bytes={len(packet)}")
            await client.write_gatt_char(
                profile.tx_characteristic_uuid,
                packet,
                response=False,
            )
            await asyncio.sleep(chunk_delay)

        if profile.rx_characteristic_uuid and ready_notification is not None:
            try:
                _debug(verbose, f"waiting for ready notification timeout={ready_timeout}s")
                await asyncio.wait_for(ready.wait(), timeout=ready_timeout)
            except asyncio.TimeoutError:
                _debug(verbose, "ready notification timed out")
                pass


async def send_packet_sequence(
    packets,
    *,
    profile: BleProfile,
    device_id: str | None = None,
    scan_timeout: float = 10.0,
    packet_delay: float = 0.01,
    response: bool = False,
    verbose: bool = False,
) -> None:
    device = await find_printer(
        profile=profile,
        device_id=device_id,
        timeout=scan_timeout,
        verbose=verbose,
    )
    packet_list = [bytes(packet) for packet in packets]

    _debug(verbose, "connecting for packet sequence")
    async with BleakClient(device) as client:
        services = client.services
        if services is None:
            services = await client.get_services()
        _debug(verbose, f"connected={client.is_connected} mtu={client.mtu_size}")

        chunk_size = max(20, client.mtu_size - 3)
        _debug(verbose, f"writing packets={len(packet_list)} chunk_size={chunk_size}")
        for packet_index, data in enumerate(packet_list, start=1):
            for chunk in _chunk(data, chunk_size):
                _debug(
                    verbose,
                    f"write packet={packet_index}/{len(packet_list)} bytes={len(chunk)}",
                )
                await client.write_gatt_char(
                    profile.tx_characteristic_uuid,
                    chunk,
                    response=response,
                )
                await asyncio.sleep(packet_delay)
