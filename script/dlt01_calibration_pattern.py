from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.exists():
    sys.path.insert(0, str(SRC))

from PIL import Image, ImageDraw, ImageFont

from bt_printers.base import RasterOptions
from bt_printers.devices.dlt01 import DLT01_DEVICE
from bt_printers.devices import dlt01_protocol as protocol


DEFAULT_OUTPUT = ROOT / "test" / "dlt01_calibration_pattern.png"


def _load_font() -> ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), 10)
    return ImageFont.load_default()


def build_design_canvas() -> Image.Image:
    width = protocol.LABEL_LENGTH_PX
    height = protocol.LABEL_WIDTH_PX
    image = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(image)
    font = _load_font()

    draw.rectangle((0, 0, width - 1, height - 1), outline=0, width=1)
    draw.line((width // 2, 0, width // 2, height - 1), fill=0)
    draw.line((0, height // 2, width - 1, height // 2), fill=0)

    for x in range(0, width, 40):
        draw.line((x, 0, x, 9), fill=0)
        draw.line((x, height - 10, x, height - 1), fill=0)
    for y in range(0, height, 24):
        draw.line((0, y, 9, y), fill=0)
        draw.line((width - 10, y, width - 1, y), fill=0)

    # Asymmetric corner markers make it obvious which physical edge was clipped.
    draw.rectangle((4, 4, 28, 28), fill=0)
    draw.rectangle((width - 29, 4, width - 5, 28), outline=0, width=3)
    draw.ellipse((4, height - 29, 28, height - 5), outline=0, width=3)
    draw.line((width - 29, height - 29, width - 5, height - 5), fill=0, width=3)
    draw.line((width - 29, height - 5, width - 5, height - 29), fill=0, width=3)

    draw.text((36, 5), "FEED", fill=0, font=font)
    draw.text((width - 78, 5), "OUTPUT", fill=0, font=font)
    draw.text((width // 2 + 5, height // 2 + 5), "DL-T01 12x40", fill=0, font=font)
    return image


def build_transport_page(
    *,
    output_edge_mm: float,
    feed_edge_mm: float,
    long_axis_offset_mm: float,
) -> Image.Image:
    calibration = DLT01_DEVICE.calibrate.with_overrides(
        output_edge_mm=output_edge_mm,
        feed_edge_mm=feed_edge_mm,
        long_axis_offset_mm=long_axis_offset_mm,
    )
    design = build_design_canvas()
    transport = design.rotate(90, expand=True)
    return calibration.apply(transport, width_px=DLT01_DEVICE.profile.width_px)


def rows_from_page(page: Image.Image) -> list[bytes]:
    return DLT01_DEVICE.prepare.image_to_rows(
        page,
        options=RasterOptions(binarization="threshold"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate or print a DL-T01 asymmetric calibration label.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--print", action="store_true", dest="should_print")
    parser.add_argument("--device", default="DL-T01")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--energy", type=lambda value: int(value, 0), default=0xFFFF)
    parser.add_argument("--output-edge-mm", type=float, default=0.0)
    parser.add_argument("--feed-edge-mm", type=float, default=0.0)
    parser.add_argument("--long-axis-offset-mm", type=float, default=0.5)
    return parser


async def _print_rows(args: argparse.Namespace, rows: list[bytes]) -> None:
    summary = await DLT01_DEVICE.printer(DLT01_DEVICE.calibrate).send_rows(
        rows,
        profile=DLT01_DEVICE.profile,
        energy=args.energy,
        device_id=args.device,
        scan_timeout=args.timeout,
        chunk_delay=0.02,
        ready_timeout=8.0,
    )
    print(f"printed width={summary.width_px} rows={summary.rows} bytes={summary.bytes_sent}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    page = build_transport_page(
        output_edge_mm=args.output_edge_mm,
        feed_edge_mm=args.feed_edge_mm,
        long_axis_offset_mm=args.long_axis_offset_mm,
    )
    rows = rows_from_page(page)
    _job, summary = DLT01_DEVICE.printer(DLT01_DEVICE.calibrate).build_job(
        rows,
        energy=args.energy,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    page.save(args.output)
    print(f"saved={args.output}")
    print(f"width={summary.width_px} rows={summary.rows} bytes={summary.bytes_sent}")
    print(f"line_packets={len(protocol.label_lines(rows))}")

    if args.should_print:
        asyncio.run(_print_rows(args, rows))


if __name__ == "__main__":
    main()
