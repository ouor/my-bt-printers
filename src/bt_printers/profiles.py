from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BleProfile:
    name: str
    service_uuids: tuple[str, ...]
    tx_characteristic_uuid: str
    rx_characteristic_uuid: str
    width_px: int
