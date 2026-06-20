from __future__ import annotations

from .dlt01 import DLT01_DEVICE, DLT01Device
from .mx10 import MX10_DEVICE, MX10Device
from .peripage import PERIPAGE_DEVICE, PeriPageDevice

_DEVICES = {
    "dl-t01": DLT01_DEVICE,
    "dlt01": DLT01_DEVICE,
    "dolewa-t01": DLT01_DEVICE,
    "label": DLT01_DEVICE,
    "mx10": MX10_DEVICE,
    "cat": MX10_DEVICE,
    "peripage": PERIPAGE_DEVICE,
    "peripage-a6p": PERIPAGE_DEVICE,
}


def get_device(name: str):
    try:
        return _DEVICES[name.lower()]
    except KeyError as exc:
        known = ", ".join(sorted(_DEVICES))
        raise ValueError(f"unknown printer device {name!r}; known devices: {known}") from exc


def known_devices() -> tuple[str, ...]:
    return tuple(sorted(_DEVICES))


__all__ = ["DLT01Device", "MX10Device", "PeriPageDevice", "get_device", "known_devices"]
