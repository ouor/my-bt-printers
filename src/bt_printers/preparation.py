from __future__ import annotations

from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageStat

from .base import Prepare, RasterOptions, TextPrepareOptions

Binarization = Literal["floyd-steinberg", "threshold"]


def _resampling_lanczos() -> int:
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS


def _dither_floyd_steinberg() -> int:
    try:
        return Image.Dither.FLOYDSTEINBERG
    except AttributeError:
        return Image.FLOYDSTEINBERG


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _load_font(font_path: str | None, font_size: int) -> ImageFont.ImageFont:
    candidates = []
    if font_path:
        candidates.append(font_path)
    candidates.extend(
        [
            r"C:\Windows\Fonts\malgun.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        ]
    )

    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), font_size)
    return ImageFont.load_default()


def _wrap_line(
    line: str,
    *,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    if not line:
        return [""]
    words = line.split(" ")
    if len(words) == 1:
        return _wrap_unspaced(line, draw=draw, font=font, max_width=max_width)

    wrapped: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        candidate_width, _ = _text_bbox(draw, candidate, font)
        if candidate_width <= max_width:
            current = candidate
            continue
        if current:
            wrapped.append(current)
        if _text_bbox(draw, word, font)[0] <= max_width:
            current = word
        else:
            split = _wrap_unspaced(word, draw=draw, font=font, max_width=max_width)
            wrapped.extend(split[:-1])
            current = split[-1]
    if current:
        wrapped.append(current)
    return wrapped


def _wrap_unspaced(
    text: str,
    *,
    draw: ImageDraw.ImageDraw,
    font: ImageFont.ImageFont,
    max_width: int,
) -> list[str]:
    wrapped: list[str] = []
    current = ""
    for char in text:
        candidate = current + char
        if current and _text_bbox(draw, candidate, font)[0] > max_width:
            wrapped.append(current)
            current = char
        else:
            current = candidate
    if current:
        wrapped.append(current)
    return wrapped or [""]


def rasterize_text(
    text: str,
    *,
    width_px: int,
    font_path: str | None = None,
    font_size: int = 28,
    margin_px: int = 16,
    line_spacing_px: int = 8,
    align: Literal["left", "center", "right"] = "left",
) -> Image.Image:
    font = _load_font(font_path, font_size)
    probe = Image.new("L", (width_px, 1), 255)
    draw = ImageDraw.Draw(probe)
    max_text_width = max(1, width_px - margin_px * 2)

    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        lines.extend(
            _wrap_line(paragraph, draw=draw, font=font, max_width=max_text_width)
        )

    heights = [_text_bbox(draw, line or " ", font)[1] for line in lines]
    line_height = max(heights + [font_size])
    height = (
        margin_px * 2
        + len(lines) * line_height
        + max(0, len(lines) - 1) * line_spacing_px
    )
    image = Image.new("L", (width_px, height), 255)
    draw = ImageDraw.Draw(image)

    y = margin_px
    for line in lines:
        line_width, _ = _text_bbox(draw, line, font)
        if align == "center":
            x = (width_px - line_width) // 2
        elif align == "right":
            x = width_px - margin_px - line_width
        else:
            x = margin_px
        draw.text((x, y), line, fill=0, font=font)
        y += line_height + line_spacing_px
    return image


def resize_image_to_width(path: str | Path, *, width_px: int) -> Image.Image:
    image = ImageOps.exif_transpose(Image.open(path)).convert("L")
    factor = width_px / image.width
    height = max(1, round(image.height * factor))
    return image.resize((width_px, height), _resampling_lanczos())


def _trim_blank_edges(image: Image.Image, *, blank_threshold: int = 250) -> Image.Image:
    gray = image.convert("L")
    mask = gray.point(lambda value: 0 if value >= blank_threshold else 255, mode="L")
    bbox = mask.getbbox()
    if bbox is None:
        return gray
    return gray.crop(bbox)


def average_density(image: Image.Image, *, trim_blank_edges: bool = False) -> float:
    gray = image.convert("L")
    if trim_blank_edges:
        gray = _trim_blank_edges(gray)
    mean = ImageStat.Stat(gray).mean[0]
    return max(0.0, min(1.0, (255.0 - mean) / 255.0))


def limit_average_density(
    image: Image.Image,
    *,
    max_average_density: float,
) -> Image.Image:
    if not 0.0 < max_average_density <= 1.0:
        raise ValueError("max_average_density must be between 0 and 1")

    gray = image.convert("L")
    density = average_density(gray, trim_blank_edges=True)
    if density <= max_average_density or density == 0.0:
        return gray

    scale = max_average_density / density
    lookup = [
        max(0, min(255, round(255 - (255 - value) * scale)))
        for value in range(256)
    ]
    return gray.point(lookup)


def image_to_rows(
    image: Image.Image,
    *,
    binarization: Binarization = "floyd-steinberg",
    threshold: int = 127,
    max_average_density: float | None = None,
) -> list[bytes]:
    gray = image.convert("L")
    if max_average_density is not None:
        gray = limit_average_density(
            gray,
            max_average_density=max_average_density,
        )

    if binarization == "threshold":
        one_bit = gray.point(lambda value: 0 if value <= threshold else 255, mode="1")
    elif binarization == "floyd-steinberg":
        one_bit = gray.convert("1", dither=_dither_floyd_steinberg())
    else:
        raise ValueError(f"unknown binarization mode: {binarization}")

    pixels = one_bit.load()
    rows: list[bytes] = []
    for y in range(one_bit.height):
        rows.append(bytes(1 if pixels[x, y] == 0 else 0 for x in range(one_bit.width)))
    return rows


class DefaultPrepare(Prepare):
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
        return image_to_rows(
            image,
            binarization=options.binarization,
            threshold=options.threshold,
            max_average_density=options.max_average_density,
        )
