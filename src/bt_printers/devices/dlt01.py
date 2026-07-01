from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from PIL import Image

from ..base import (
    Calibrate,
    Prepare,
    Print,
    PrintSummary,
    RasterOptions,
    TextPrepareOptions,
)
from ..calibration import PrintCalibration, with_overrides
from ..preparation import image_to_rows, rasterize_text, resize_image_to_width
from ..profiles import BleProfile
from . import dlt01_protocol as protocol

DLT01_BLE_PROFILE = BleProfile(
    name="dlt01",
    service_uuids=("0000ffe6-0000-1000-8000-00805f9b34fb",),
    tx_characteristic_uuid="0000ffe1-0000-1000-8000-00805f9b34fb",
    rx_characteristic_uuid="0000ffe2-0000-1000-8000-00805f9b34fb",
    width_px=protocol.LABEL_WIDTH_PX,
)

DLT01_CALIBRATION = PrintCalibration(
    paper_width_mm=12.0,
    left_margin_mm=0.0,
    right_margin_mm=0.0,
    top_margin_mm=0.0,
    bottom_margin_mm=0.0,
    dots_per_mm_x=8.0,
    dots_per_mm_y=8.0,
    feed_lines=0,
    set_paper_repeats=0,
)

_DLT01_CONFIG_FIELDS = frozenset(PrintCalibration.__dataclass_fields__)
DLT01_DEFAULT_OUTPUT_EDGE_MM = 0.0
DLT01_DEFAULT_FEED_EDGE_MM = 0.0
# Lead-in from the output/leading edge to the first content row (see
# _anchor_to_output_edge). Calibrated on the 12x40mm stock: at this 0.5mm lead-in
# content lands with a clean ~1.5mm physical margin at the output edge. More
# leading blank makes the printer advance to the next label gap (~8mm jump), so
# content is anchored to the output edge and the outer ~1.5mm of the 40mm axis is
# treated as an unusable safe zone.
DLT01_DEFAULT_LONG_AXIS_OFFSET_MM = 0.5

# Reserve the unusable ~1.5mm at the feed/trailing end of the 40mm axis so content
# is composed inside the printable region. The output/leading end is handled by the
# mechanical long-axis offset above; adding blank rows there would trip the
# printer's label-gap advance (~8mm jump), so only the feed end is reserved here.
DLT01_UNUSABLE_END_MM = 1.5
_USABLE_FEED_RESERVE_PX = round(
    DLT01_UNUSABLE_END_MM * protocol.LABEL_LENGTH_PX / protocol.LABEL_LENGTH_MM
)


def _debug(verbose: bool, message: str) -> None:
    if verbose:
        print(f"[dlt01] {message}", file=sys.stderr)


def _is_direct_address(value: str) -> bool:
    return value.count(":") == 5 and all(len(part) == 2 for part in value.split(":"))


def _matches_target(
    device: BLEDevice,
    adv_data: AdvertisementData,
    target: str | None,
) -> bool:
    names = {name.casefold() for name in (device.name, adv_data.local_name) if name}
    services = {uuid.casefold() for uuid in adv_data.service_uuids}
    if target:
        wanted = target.casefold()
        return device.address.casefold() == wanted or wanted in names
    return (
        "dl-t01" in names
        or "0000ffe6-0000-1000-8000-00805f9b34fb" in services
    )


async def _find_dlt01(
    device_id: str | None,
    *,
    timeout: float,
    verbose: bool = False,
) -> BLEDevice | str:
    if device_id and _is_direct_address(device_id):
        _debug(verbose, f"using direct address {device_id}")
        return device_id

    _debug(verbose, f"finding DL-T01 target={device_id or 'auto'} timeout={timeout}s")
    device = await BleakScanner.find_device_by_filter(
        lambda device, adv_data: _matches_target(device, adv_data, device_id),
        timeout=timeout,
    )
    if device is None:
        target = device_id or "DL-T01"
        raise RuntimeError(f"could not find BLE device {target!r}")
    _debug(verbose, f"found {device.name or '(unknown)'} {device.address}")
    return device


def _device_address(device: BLEDevice | str) -> str:
    return device if isinstance(device, str) else device.address


def _fit_on_canvas(image: Image.Image, *, width_px: int, height_px: int) -> Image.Image:
    gray = image.convert("L")
    factor = min(width_px / gray.width, height_px / gray.height)
    size = (
        max(1, round(gray.width * factor)),
        max(1, round(gray.height * factor)),
    )
    resized = gray.resize(size, Image.Resampling.LANCZOS)
    canvas = Image.new("L", (width_px, height_px), 255)
    canvas.paste(resized, ((width_px - size[0]) // 2, (height_px - size[1]) // 2))
    return canvas


def _label_to_print_orientation(image: Image.Image) -> Image.Image:
    return image.rotate(90, expand=True)


def _fixed_transport_page(image: Image.Image) -> Image.Image:
    page = Image.new("L", (protocol.LABEL_WIDTH_PX, protocol.LABEL_LENGTH_PX), 255)
    source = image.convert("L").crop(
        (
            0,
            0,
            min(image.width, protocol.LABEL_WIDTH_PX),
            min(image.height, protocol.LABEL_LENGTH_PX),
        )
    )
    page.paste(source, (0, 0))
    return page


def _edge_mm_to_px(value_mm: float, *, dots_per_mm: float) -> int:
    if value_mm < 0:
        raise ValueError("DL-T01 long-axis calibration values must be non-negative")
    return round(value_mm * dots_per_mm)


def _anchor_to_output_edge(image: Image.Image, *, lead_in_px: int) -> Image.Image:
    """Anchor content to the output/leading edge along the feed axis.

    The whole label is shifted so the first non-blank row sits ``lead_in_px`` from
    the output/leading edge (transport y=0). This caps the leading blank to a
    small, safe amount regardless of how much blank the content carries at the
    output end: too much leading blank makes this printer advance to the next
    label gap (~8mm jump). Slack is left at the feed/trailing end as margin.
    """
    gray = image.convert("L")
    mask = gray.point(lambda value: 0 if value >= 250 else 255)
    bbox = mask.getbbox()
    if bbox is None:
        return image
    shift = lead_in_px - bbox[1]
    if shift == 0:
        return image
    page = Image.new("L", (image.width, image.height), 255)
    page.paste(image, (0, shift))
    return page


def _fit_to_long_axis_bounds(
    image: Image.Image,
    *,
    output_edge_px: int,
    feed_edge_px: int,
) -> Image.Image:
    if output_edge_px == 0 and feed_edge_px == 0:
        return image

    available_length = protocol.LABEL_LENGTH_PX - output_edge_px - feed_edge_px
    if available_length < 1:
        raise ValueError("DL-T01 output/feed edge calibration leaves no printable length")

    page = Image.new("L", (protocol.LABEL_WIDTH_PX, protocol.LABEL_LENGTH_PX), 255)
    top = output_edge_px
    bottom = output_edge_px + available_length
    fitted = image.crop((0, top, protocol.LABEL_WIDTH_PX, bottom))
    page.paste(fitted, (0, output_edge_px))
    return page


@dataclass(frozen=True)
class DLT01Prepare(Prepare):
    def rasterize_text(
        self,
        text: str,
        *,
        width_px: int,
        options: TextPrepareOptions,
    ) -> Image.Image:
        design_height = protocol.LABEL_WIDTH_PX
        # Compose within the usable long-axis region: the feed end keeps a blank
        # safe zone, the output end is anchored to the edge (its margin comes from
        # the mechanical offset, not blank rows).
        usable_width = protocol.LABEL_LENGTH_PX - _USABLE_FEED_RESERVE_PX
        font_size = options.font_size
        margin_px = min(options.margin_px, 8)

        while True:
            rendered = rasterize_text(
                text,
                width_px=usable_width,
                font_path=options.font_path,
                font_size=font_size,
                margin_px=margin_px,
                line_spacing_px=min(options.line_spacing_px, 4),
                align=options.align,
            )
            if rendered.height <= design_height or font_size <= 8:
                break
            font_size -= 2

        canvas = Image.new("L", (protocol.LABEL_LENGTH_PX, design_height), 255)
        y = max(0, (design_height - rendered.height) // 2)
        source = rendered.crop((0, 0, usable_width, min(rendered.height, design_height)))
        canvas.paste(source, (_USABLE_FEED_RESERVE_PX, y))
        return _label_to_print_orientation(canvas)

    def resize_image_to_width(self, path: str | Path, *, width_px: int) -> Image.Image:
        usable_width = protocol.LABEL_LENGTH_PX - _USABLE_FEED_RESERVE_PX
        image = resize_image_to_width(path, width_px=usable_width)
        design = _fit_on_canvas(
            image,
            width_px=usable_width,
            height_px=protocol.LABEL_WIDTH_PX,
        )
        canvas = Image.new("L", (protocol.LABEL_LENGTH_PX, protocol.LABEL_WIDTH_PX), 255)
        canvas.paste(design, (_USABLE_FEED_RESERVE_PX, 0))
        return _label_to_print_orientation(canvas)

    def image_to_rows(
        self,
        image: Image.Image,
        *,
        options: RasterOptions,
    ) -> list[bytes]:
        return image_to_rows(
            image,
            binarization=options.binarization,
            threshold=options.threshold,
            max_average_density=options.max_average_density,
        )


@dataclass(frozen=True)
class DLT01Calibrate(Calibrate):
    config: PrintCalibration = DLT01_CALIBRATION
    output_edge_mm: float = DLT01_DEFAULT_OUTPUT_EDGE_MM
    feed_edge_mm: float = DLT01_DEFAULT_FEED_EDGE_MM
    long_axis_offset_mm: float = DLT01_DEFAULT_LONG_AXIS_OFFSET_MM

    def with_overrides(self, **kwargs) -> DLT01Calibrate:
        config = kwargs.pop("config", self.config)
        config_updates = {
            key: kwargs.pop(key)
            for key in tuple(kwargs)
            if key in _DLT01_CONFIG_FIELDS
        }
        if config_updates:
            config = with_overrides(config, **config_updates)

        device_updates = {}
        for key in ("output_edge_mm", "feed_edge_mm", "long_axis_offset_mm"):
            if key in kwargs:
                device_updates[key] = kwargs.pop(key)

        if kwargs:
            names = ", ".join(sorted(kwargs))
            raise TypeError(f"unknown DL-T01 calibration override(s): {names}")

        return replace(self, config=config, **device_updates)

    def long_axis_insets_px(self) -> tuple[int, int]:
        return (
            _edge_mm_to_px(
                self.output_edge_mm,
                dots_per_mm=self.config.dots_per_mm_y,
            ),
            _edge_mm_to_px(
                self.feed_edge_mm,
                dots_per_mm=self.config.dots_per_mm_y,
            ),
        )

    def image_width_px(
        self,
        *,
        max_width_px: int,
        override_width_px: int | None = None,
    ) -> int:
        if override_width_px is not None and override_width_px != protocol.LABEL_WIDTH_PX:
            raise ValueError("DL-T01 uses a fixed 12mm / 96px label width")
        return min(max_width_px, protocol.LABEL_WIDTH_PX)

    def long_axis_offset_px(self) -> int:
        return round(self.long_axis_offset_mm * self.config.dots_per_mm_y)

    def apply(self, image: Image.Image, *, width_px: int) -> Image.Image:
        if width_px != protocol.LABEL_WIDTH_PX:
            raise ValueError("DL-T01 uses a fixed 12mm / 96px label width")
        output_edge_px, feed_edge_px = self.long_axis_insets_px()
        page = _fixed_transport_page(image)
        page = _anchor_to_output_edge(page, lead_in_px=self.long_axis_offset_px())
        return _fit_to_long_axis_bounds(
            page,
            output_edge_px=output_edge_px,
            feed_edge_px=feed_edge_px,
        )


@dataclass(frozen=True)
class DLT01Print(Print):
    calibration: DLT01Calibrate

    def build_job(
        self,
        rows: Sequence[Sequence[int]],
        *,
        energy: int,
    ) -> tuple[bytes, PrintSummary]:
        self._validate_rows(rows)
        packets = protocol.build_print_packets(rows, energy=energy)
        job = b"".join(packets)
        return job, PrintSummary(
            width_px=len(rows[0]),
            rows=len(rows),
            bytes_sent=len(job),
        )

    def _validate_rows(self, rows: Sequence[Sequence[int]]) -> None:
        if len(rows) != protocol.LABEL_LENGTH_PX:
            raise ValueError(
                f"DL-T01 expects exactly {protocol.LABEL_LENGTH_PX} rows "
                f"for one 40mm label, got {len(rows)}"
            )
        if not rows or len(rows[0]) != protocol.LABEL_WIDTH_PX:
            raise ValueError(
                f"DL-T01 expects {protocol.LABEL_WIDTH_PX}px-wide rows"
            )

    async def _configure(
        self,
        client: BleakClient,
        *,
        profile: BleProfile,
        timeout: float,
        verbose: bool = False,
    ) -> asyncio.Queue[bytes]:
        events: asyncio.Queue[bytes] = asyncio.Queue()
        ready: asyncio.Queue[bytes] = asyncio.Queue()

        def on_notify(_sender, payload: bytearray) -> None:
            data = bytes(payload)
            _debug(verbose, f"notify {data.hex(' ')}")
            if data[:2] in (
                protocol.LOST_PACKET,
                protocol.PRINTING_PAUSED,
                protocol.PRINTING_FINISHED,
            ):
                events.put_nowait(data)
            else:
                # 5a01 hardware-info reply, 5a02 status heartbeat, command echoes.
                ready.put_nowait(data)

        await client.start_notify(profile.rx_characteristic_uuid, on_notify)
        _debug(verbose, f"start notify rx={profile.rx_characteristic_uuid}")
        # The DOLEWA app opens every print by announcing hardware_info and waiting
        # for the printer's first reply. It performs no 5a0a/5a0b handshake.
        _debug(verbose, "write hardware_info")
        await client.write_gatt_char(
            profile.tx_characteristic_uuid,
            protocol.hardware_info(),
            response=False,
        )
        try:
            _debug(verbose, f"waiting for first reply timeout={timeout}s")
            await asyncio.wait_for(ready.get(), timeout=timeout)
        except asyncio.TimeoutError:
            _debug(verbose, "first reply timed out")
            pass
        return events

    async def _send_line_packets(
        self,
        client: BleakClient,
        *,
        profile: BleProfile,
        packets: Sequence[bytes],
        events: asyncio.Queue[bytes],
        chunk_delay: float,
        ready_timeout: float,
        verbose: bool = False,
    ) -> None:
        _debug(verbose, f"writing line packets={len(packets)}")
        for index, packet in enumerate(packets, start=1):
            _debug(verbose, f"write line={index}/{len(packets)} bytes={len(packet)}")
            await client.write_gatt_char(
                profile.tx_characteristic_uuid,
                packet,
                response=False,
            )
            await asyncio.sleep(chunk_delay)

        # Keep waiting only after all rows are sent for devices that emit a finish
        # notification after the final packet.
        try:
            _debug(verbose, f"waiting for finish notification timeout={ready_timeout}s")
            while True:
                event = await asyncio.wait_for(
                    events.get(),
                    timeout=ready_timeout,
                )
                if event[:2] == protocol.PRINTING_FINISHED:
                    _debug(verbose, "finish notification received")
                    return
        except TimeoutError:
            _debug(verbose, "finish notification timed out")
            return

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
        self._validate_rows(rows)
        lines = protocol.label_lines(rows)
        packets = [protocol.print_line(index, line) for index, line in enumerate(lines)]
        start_events = protocol.print_status_sequence(len(lines))
        bytes_sent = (
            len(protocol.hardware_info())
            + len(protocol.density(protocol.energy_to_density(energy)))
            + len(protocol.print_prepare())
            + sum(len(start_event) for start_event in start_events)
            + sum(len(packet) for packet in packets)
            + len(protocol.print_status(len(lines), end=True))
        )
        summary = PrintSummary(
            width_px=len(rows[0]),
            rows=len(rows),
            bytes_sent=bytes_sent,
        )

        device = await _find_dlt01(
            device_id,
            timeout=scan_timeout,
            verbose=verbose,
        )

        _debug(verbose, "connecting for print job")
        async with BleakClient(device, timeout=scan_timeout) as client:
            _debug(verbose, f"connected={client.is_connected} mtu={client.mtu_size}")
            events = await self._configure(
                client,
                profile=profile,
                timeout=ready_timeout,
                verbose=verbose,
            )
            # Set burn density explicitly (12-byte 5a0c) instead of relying on the
            # value the app happened to persist on the device.
            await client.write_gatt_char(
                profile.tx_characteristic_uuid,
                protocol.density(protocol.energy_to_density(energy)),
                response=False,
            )
            _debug(verbose, f"write density={protocol.energy_to_density(energy)}")
            await client.write_gatt_char(
                profile.tx_characteristic_uuid,
                protocol.print_prepare(),
                response=False,
            )
            _debug(verbose, "write print_prepare")
            for event in start_events:
                _debug(verbose, f"write start_event bytes={len(event)}")
                await client.write_gatt_char(
                    profile.tx_characteristic_uuid,
                    event,
                    response=False,
                )
            # All raster packets are sent before waiting for the finish event so
            # an early notification cannot truncate the label.
            await self._send_line_packets(
                client,
                profile=profile,
                packets=packets,
                events=events,
                chunk_delay=chunk_delay,
                ready_timeout=ready_timeout,
                verbose=verbose,
            )
            _debug(verbose, "write end_event")
            await client.write_gatt_char(
                profile.tx_characteristic_uuid,
                protocol.print_status(len(lines), end=True),
                response=False,
            )
        return summary


@dataclass(frozen=True)
class DLT01Device:
    name: str = "dlt01"
    profile: BleProfile = DLT01_BLE_PROFILE
    prepare: DLT01Prepare = DLT01Prepare()
    calibrate: DLT01Calibrate = DLT01Calibrate()

    def printer(self, calibration: Calibrate) -> DLT01Print:
        if not isinstance(calibration, DLT01Calibrate):
            raise TypeError("DLT01Device requires DLT01Calibrate")
        return DLT01Print(calibration=calibration)


DLT01_DEVICE = DLT01Device()
