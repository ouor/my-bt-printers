from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..base import Calibrate, Print, PrintSummary
from ..ble import send_print_job
from ..calibration import (
    PrintCalibration,
    apply_calibration,
    calibrated_image_width_px,
    with_overrides,
)
from ..preparation import DefaultPrepare
from ..profiles import BleProfile
from .mx10_protocol import PRINTER_READY_NOTIFICATION, build_image_job

MX10_BLE_PROFILE = BleProfile(
    name="mx10",
    service_uuids=(
        "0000ae30-0000-1000-8000-00805f9b34fb",
        "0000af30-0000-1000-8000-00805f9b34fb",
    ),
    tx_characteristic_uuid="0000ae01-0000-1000-8000-00805f9b34fb",
    rx_characteristic_uuid="0000ae02-0000-1000-8000-00805f9b34fb",
    width_px=384,
)

MX10_CALIBRATION = PrintCalibration(
    paper_width_mm=55.0,
    left_margin_mm=3.0,
    right_margin_mm=3.0,
    top_margin_mm=0.0,
    bottom_margin_mm=4.0,
    dots_per_mm_x=8.0,
    dots_per_mm_y=8.0,
    feed_lines=0,
    set_paper_repeats=1,
)


@dataclass(frozen=True)
class MX10Prepare(DefaultPrepare):
    pass


@dataclass(frozen=True)
class MX10Calibrate(Calibrate):
    config: PrintCalibration = MX10_CALIBRATION

    def with_overrides(self, **kwargs) -> MX10Calibrate:
        return MX10Calibrate(config=with_overrides(self.config, **kwargs))

    def image_width_px(
        self,
        *,
        max_width_px: int,
        override_width_px: int | None = None,
    ) -> int:
        return calibrated_image_width_px(
            calibration=self.config,
            max_width_px=max_width_px,
            override_width_px=override_width_px,
        )

    def apply(self, image: Image.Image, *, width_px: int) -> Image.Image:
        return apply_calibration(image, width_px=width_px, calibration=self.config)


@dataclass(frozen=True)
class MX10Print(Print):
    calibration: MX10Calibrate

    def build_job(
        self,
        rows: Sequence[Sequence[int]],
        *,
        energy: int,
    ) -> tuple[bytes, PrintSummary]:
        if not rows:
            raise ValueError("print job must contain at least one row")

        width_px = len(rows[0])
        job = build_image_job(
            rows,
            energy=energy,
            feed_lines=self.calibration.config.feed_lines,
            width_px=width_px,
            set_paper_repeats=self.calibration.config.set_paper_repeats,
        )
        return job, PrintSummary(
            width_px=width_px,
            rows=len(rows),
            bytes_sent=len(job),
        )

    async def send_rows(
        self,
        rows: Sequence[Sequence[int]],
        *,
        profile: BleProfile,
        energy: int,
        device_id: str | None,
        scan_timeout: float,
        chunk_delay: float,
        ready_timeout: float,
        verbose: bool = False,
    ) -> PrintSummary:
        job, summary = self.build_job(rows, energy=energy)
        await send_print_job(
            job,
            profile=profile,
            device_id=device_id,
            scan_timeout=scan_timeout,
            chunk_delay=chunk_delay,
            ready_timeout=ready_timeout,
            ready_notification=PRINTER_READY_NOTIFICATION,
            verbose=verbose,
        )
        return summary


@dataclass(frozen=True)
class MX10Device:
    name: str = "mx10"
    profile: BleProfile = MX10_BLE_PROFILE
    prepare: MX10Prepare = MX10Prepare()
    calibrate: MX10Calibrate = MX10Calibrate()

    def printer(self, calibration: Calibrate) -> MX10Print:
        if not isinstance(calibration, MX10Calibrate):
            raise TypeError("MX10Device requires MX10Calibrate")
        return MX10Print(calibration=calibration)


MX10_DEVICE = MX10Device()
