from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..base import (
    Calibrate,
    Prepare,
    Print,
    PrintSummary,
    RasterOptions,
    TextPrepareOptions,
)
from ..ble import send_packet_sequence
from ..calibration import (
    PrintCalibration,
    apply_calibration,
    calibrated_image_width_px,
    with_overrides,
)
from ..preparation import image_to_rows, rasterize_text, resize_image_to_width
from ..profiles import BleProfile
from .peripage_protocol import build_image_job, build_image_packets

PERIPAGE_MAX_AVERAGE_DENSITY = 0.42

PERIPAGE_A6P_PROFILE = BleProfile(
    name="peripage",
    service_uuids=(
        "0000fee7-0000-1000-8000-00805f9b34fb",
        "0000ff00-0000-1000-8000-00805f9b34fb",
        "49535343-fe7d-4ae5-8fa9-9fafd205e455",
    ),
    tx_characteristic_uuid="0000ff02-0000-1000-8000-00805f9b34fb",
    rx_characteristic_uuid="0000ff01-0000-1000-8000-00805f9b34fb",
    width_px=576,
)

PERIPAGE_A6P_CALIBRATION = PrintCalibration(
    paper_width_mm=48.5,
    left_margin_mm=0.0,
    right_margin_mm=0.0,
    top_margin_mm=0.0,
    bottom_margin_mm=12.5,
    dots_per_mm_x=576 / 48.5,
    dots_per_mm_y=576 / 48.5,
    feed_lines=0,
    set_paper_repeats=0,
)


@dataclass(frozen=True)
class PeriPagePrepare(Prepare):
    def rasterize_text(
        self,
        text: str,
        *,
        width_px: int,
        options: TextPrepareOptions,
    ) -> Image.Image:
        return rasterize_text(
            text,
            width_px=width_px,
            font_path=options.font_path,
            font_size=options.font_size,
            margin_px=options.margin_px,
            line_spacing_px=options.line_spacing_px,
            align=options.align,
        )

    def resize_image_to_width(self, path: str | Path, *, width_px: int) -> Image.Image:
        return resize_image_to_width(path, width_px=width_px)

    def image_to_rows(
        self,
        image: Image.Image,
        *,
        options: RasterOptions,
    ) -> list[bytes]:
        max_average_density = options.max_average_density
        if max_average_density is None and options.binarization == "floyd-steinberg":
            max_average_density = PERIPAGE_MAX_AVERAGE_DENSITY

        return image_to_rows(
            image,
            binarization=options.binarization,
            threshold=options.threshold,
            max_average_density=max_average_density,
        )


@dataclass(frozen=True)
class PeriPageCalibrate(Calibrate):
    config: PrintCalibration = PERIPAGE_A6P_CALIBRATION

    def with_overrides(self, **kwargs) -> PeriPageCalibrate:
        return PeriPageCalibrate(config=with_overrides(self.config, **kwargs))

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
class PeriPagePrint(Print):
    calibration: PeriPageCalibrate
    row_bytes: int = 72

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
            row_width_px=width_px,
            row_bytes=self.row_bytes,
            energy=energy,
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
        _job, summary = self.build_job(rows, energy=energy)
        packets = build_image_packets(
            rows,
            row_width_px=summary.width_px,
            row_bytes=self.row_bytes,
            energy=energy,
        )
        await send_packet_sequence(
            packets,
            profile=profile,
            device_id=device_id,
            scan_timeout=scan_timeout,
            packet_delay=chunk_delay,
            response=False,
            verbose=verbose,
        )
        return summary


@dataclass(frozen=True)
class PeriPageDevice:
    name: str = "peripage"
    profile: BleProfile = PERIPAGE_A6P_PROFILE
    prepare: PeriPagePrepare = PeriPagePrepare()
    calibrate: PeriPageCalibrate = PeriPageCalibrate()

    def printer(self, calibration: Calibrate) -> PeriPagePrint:
        if not isinstance(calibration, PeriPageCalibrate):
            raise TypeError("PeriPageDevice requires PeriPageCalibrate")
        return PeriPagePrint(calibration=calibration)


PERIPAGE_DEVICE = PeriPageDevice()
