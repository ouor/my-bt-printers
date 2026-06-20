from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .base import RasterOptions, TextPrepareOptions
from .ble import inspect_device, scan_devices
from .devices import get_device, known_devices


def _energy(value: str) -> int:
    try:
        parsed = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("energy must be an integer or hex value") from exc
    if not 0 <= parsed <= 0xFFFF:
        raise argparse.ArgumentTypeError("energy must be between 0x0000 and 0xffff")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    default_device = get_device("mx10")

    parser = argparse.ArgumentParser(
        prog="bt-printer",
        description="Print to small Bluetooth LE thermal printers.",
    )
    parser.add_argument(
        "--printer",
        dest="profile",
        default=default_device.name,
        help=f"printer model to use; known: {', '.join(known_devices())}",
    )
    parser.add_argument("--profile", dest="profile", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="scan nearby BLE devices")
    scan.add_argument("--timeout", type=float, default=10.0)

    inspect = subparsers.add_parser("inspect", help="inspect printer GATT services")
    inspect.add_argument(
        "--device",
        help="BLE name or address; omitted means auto-discover",
    )
    inspect.add_argument("--timeout", type=float, default=10.0)

    text = subparsers.add_parser("print-text", help="render text and print it")
    text.add_argument("text", help="text to print")
    _add_common_print_args(text)
    text.add_argument("--font", help="path to a TrueType/OpenType font")
    text.add_argument("--font-size", type=int, default=28)
    text.add_argument("--align", choices=("left", "center", "right"), default="left")

    image = subparsers.add_parser("print-image", help="print an image file")
    image.add_argument("path", type=Path, help="image path")
    image.add_argument(
        "--binarization",
        choices=("floyd-steinberg", "threshold"),
        default="floyd-steinberg",
    )
    _add_common_print_args(image)

    return parser


def _add_common_print_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--device",
        help="BLE name or address; omitted means auto-discover",
    )
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--energy", type=_energy, default=0xFFFF)


async def _run(args: argparse.Namespace) -> None:
    device = get_device(args.profile)
    profile = device.profile

    if args.command == "scan":
        devices = await scan_devices(timeout=args.timeout)
        for device in devices:
            services = ",".join(device.service_uuids)
            print(
                f"{device.name}\t{device.address}\trssi={device.rssi}\tservices={services}"
            )
        print(f"found={len(devices)}")
        return

    if args.command == "inspect":
        lines = await inspect_device(
            profile=profile,
            device_id=args.device,
            timeout=args.timeout,
        )
        print("\n".join(lines))
        return

    if args.command == "print-text":
        calibration = device.calibrate
        image = device.prepare.rasterize_text(
            args.text,
            width_px=profile.width_px,
            options=TextPrepareOptions(
                font_path=args.font,
                font_size=args.font_size,
                align=args.align,
            ),
        )
        image = calibration.apply(image, width_px=profile.width_px)
        rows = device.prepare.image_to_rows(
            image,
            options=RasterOptions(binarization="threshold"),
        )
    elif args.command == "print-image":
        calibration = device.calibrate
        image_width_px = calibration.image_width_px(
            max_width_px=profile.width_px,
        )
        image = device.prepare.resize_image_to_width(
            args.path,
            width_px=image_width_px,
        )
        image = calibration.apply(image, width_px=profile.width_px)
        rows = device.prepare.image_to_rows(
            image,
            options=RasterOptions(
                binarization=args.binarization,
            ),
        )
    else:
        raise RuntimeError(f"unhandled command: {args.command}")

    summary = await device.printer(calibration).send_rows(
        rows,
        profile=profile,
        energy=args.energy,
        device_id=args.device,
        scan_timeout=args.timeout,
        chunk_delay=0.02,
        ready_timeout=8.0,
    )
    print(f"width={summary.width_px} rows={summary.rows} bytes={summary.bytes_sent}")
    print("done")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(_run(args))
