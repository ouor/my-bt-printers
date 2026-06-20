from __future__ import annotations

from dataclasses import dataclass, replace

from PIL import Image


@dataclass(frozen=True)
class PrintCalibration:
    paper_width_mm: float = 55.0
    left_margin_mm: float = 3.0
    right_margin_mm: float = 3.0
    top_margin_mm: float = 0.0
    bottom_margin_mm: float = 4.0
    dots_per_mm_x: float = 8.0
    dots_per_mm_y: float = 8.0
    feed_lines: int = 0
    set_paper_repeats: int = 1

def with_overrides(calibration: PrintCalibration, **kwargs) -> PrintCalibration:
    return replace(calibration, **kwargs)


def byte_aligned_width(px: int) -> int:
    return max(8, round(px / 8) * 8)


def calibrated_image_width_px(
    *,
    calibration: PrintCalibration,
    max_width_px: int,
    override_width_px: int | None = None,
) -> int:
    if override_width_px is not None:
        if override_width_px < 1:
            raise ValueError("image width must be positive")
        if override_width_px > max_width_px:
            raise ValueError(
                f"image width {override_width_px}px exceeds profile max {max_width_px}px"
            )
        return override_width_px

    image_width_mm = (
        calibration.paper_width_mm
        - calibration.left_margin_mm
        - calibration.right_margin_mm
    )
    if image_width_mm <= 0:
        raise ValueError("paper width must be larger than left + right margins")

    desired_width = byte_aligned_width(round(image_width_mm * calibration.dots_per_mm_x))
    return min(desired_width, max_width_px)


def add_vertical_margins(
    image: Image.Image,
    *,
    width_px: int,
    top_margin_mm: float,
    bottom_margin_mm: float,
    dots_per_mm_y: float,
) -> Image.Image:
    if image.width > width_px:
        raise ValueError(
            f"image width must not exceed page width ({image.width} > {width_px})"
        )

    top_px = max(0, round(top_margin_mm * dots_per_mm_y))
    bottom_px = max(0, round(bottom_margin_mm * dots_per_mm_y))
    page = Image.new("L", (width_px, top_px + image.height + bottom_px), 255)
    x = (width_px - image.width) // 2
    page.paste(image, (x, top_px))
    return page


def apply_calibration(
    image: Image.Image,
    *,
    width_px: int,
    calibration: PrintCalibration,
) -> Image.Image:
    return add_vertical_margins(
        image,
        width_px=width_px,
        top_margin_mm=calibration.top_margin_mm,
        bottom_margin_mm=calibration.bottom_margin_mm,
        dots_per_mm_y=calibration.dots_per_mm_y,
    )
