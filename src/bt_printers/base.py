from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image

from .profiles import BleProfile

Binarization = Literal["floyd-steinberg", "threshold"]
TextAlign = Literal["left", "center", "right"]


@dataclass(frozen=True)
class TextPrepareOptions:
    font_path: str | None = None
    font_size: int = 28
    margin_px: int = 16
    line_spacing_px: int = 8
    align: TextAlign = "left"


@dataclass(frozen=True)
class RasterOptions:
    binarization: Binarization = "floyd-steinberg"
    threshold: int = 127
    max_average_density: float | None = None


@dataclass(frozen=True)
class PrintSummary:
    width_px: int
    rows: int
    bytes_sent: int


class Prepare(ABC):
    @abstractmethod
    def rasterize_text(
        self,
        text: str,
        *,
        width_px: int,
        options: TextPrepareOptions,
    ) -> Image.Image:
        raise NotImplementedError

    @abstractmethod
    def resize_image_to_width(self, path: str | Path, *, width_px: int) -> Image.Image:
        raise NotImplementedError

    @abstractmethod
    def image_to_rows(
        self,
        image: Image.Image,
        *,
        options: RasterOptions,
    ) -> list[bytes]:
        raise NotImplementedError


class Calibrate(ABC):
    @abstractmethod
    def with_overrides(self, **kwargs) -> Calibrate:
        raise NotImplementedError

    @abstractmethod
    def image_width_px(
        self,
        *,
        max_width_px: int,
        override_width_px: int | None = None,
    ) -> int:
        raise NotImplementedError

    @abstractmethod
    def apply(self, image: Image.Image, *, width_px: int) -> Image.Image:
        raise NotImplementedError


class Print(ABC):
    @abstractmethod
    def build_job(
        self,
        rows: Sequence[Sequence[int]],
        *,
        energy: int,
    ) -> tuple[bytes, PrintSummary]:
        raise NotImplementedError

    @abstractmethod
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
    ) -> PrintSummary:
        raise NotImplementedError
