from __future__ import annotations

from pathlib import Path

from PIL import Image

from .calibration import add_vertical_margins as _add_vertical_margins
from .preparation import (
    Binarization,
    image_to_rows,
    rasterize_text,
    resize_image_to_width,
)


def render_text_image(*args, **kwargs):
    if "width" in kwargs:
        kwargs["width_px"] = kwargs.pop("width")
    if "margin" in kwargs:
        kwargs["margin_px"] = kwargs.pop("margin")
    if "line_spacing" in kwargs:
        kwargs["line_spacing_px"] = kwargs.pop("line_spacing")
    return rasterize_text(*args, **kwargs)


def load_image(path: str | Path, *, width: int) -> Image.Image:
    return resize_image_to_width(path, width_px=width)


def add_vertical_margins(
    image: Image.Image,
    *,
    width: int,
    top_margin_mm: float,
    bottom_margin_mm: float,
    dots_per_mm_y: float,
) -> Image.Image:
    return _add_vertical_margins(
        image,
        width_px=width,
        top_margin_mm=top_margin_mm,
        bottom_margin_mm=bottom_margin_mm,
        dots_per_mm_y=dots_per_mm_y,
    )


def load_image_page(
    path: str | Path,
    *,
    print_width_px: int,
    image_width_px: int,
    top_margin_mm: float,
    bottom_margin_mm: float,
    dots_per_mm_y: float,
) -> Image.Image:
    if image_width_px > print_width_px:
        raise ValueError(
            f"image_width_px must not exceed print_width_px "
            f"({image_width_px} > {print_width_px})"
        )
    image = resize_image_to_width(path, width_px=image_width_px)
    return _add_vertical_margins(
        image,
        width_px=print_width_px,
        top_margin_mm=top_margin_mm,
        bottom_margin_mm=bottom_margin_mm,
        dots_per_mm_y=dots_per_mm_y,
    )
