from __future__ import annotations

from collections.abc import Iterable, Sequence
from math import ceil

PRINT_WIDTH = 384


def _crc8(data: Iterable[int]) -> int:
    crc = 0
    for value in data:
        crc ^= value & 0xFF
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _command(opcode: int, payload: bytes | bytearray = b"") -> bytes:
    if len(payload) > 0xFF:
        raise ValueError("payload is too large for one printer command")
    return bytes((0x51, 0x78, opcode & 0xFF, 0x00, len(payload), 0x00)) + bytes(
        payload
    ) + bytes((_crc8(payload), 0xFF))


CMD_GET_DEV_STATE = _command(0xA3, b"\x00")
CMD_SET_QUALITY_200_DPI = _command(0xA4, b"\x32")
CMD_GET_DEV_INFO = _command(0xA8, b"\x00")
CMD_LATTICE_START = _command(
    0xA6,
    bytes((0xAA, 0x55, 0x17, 0x38, 0x44, 0x5F, 0x5F, 0x5F, 0x44, 0x38, 0x2C)),
)
CMD_LATTICE_END = _command(
    0xA6,
    bytes((0xAA, 0x55, 0x17, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x17)),
)
CMD_SET_PAPER = _command(0xA1, b"\x30\x00")

PRINTER_READY_NOTIFICATION = b"\x51\x78\xae\x01\x01\x00\x00\x00\xff"


def cmd_feed_paper(lines: int) -> bytes:
    return _command(0xBD, bytes((lines & 0xFF,)))


def cmd_set_energy(value: int) -> bytes:
    if not 0 <= value <= 0xFFFF:
        raise ValueError("energy must be between 0x0000 and 0xffff")
    return _command(0xAF, bytes(((value >> 8) & 0xFF, value & 0xFF)))


def cmd_apply_energy() -> bytes:
    return _command(0xBE, b"\x01")


def _encode_run_length_repetition(count: int, value: int) -> list[int]:
    encoded: list[int] = []
    while count > 0x7F:
        encoded.append(0x7F | ((value & 1) << 7))
        count -= 0x7F
    if count:
        encoded.append(count | ((value & 1) << 7))
    return encoded


def _run_length_encode(row: Sequence[int]) -> bytes:
    encoded: list[int] = []
    previous = -1
    count = 0
    for value in row:
        bit = 1 if value else 0
        if bit == previous:
            count += 1
        else:
            encoded.extend(_encode_run_length_repetition(count, previous))
            previous = bit
            count = 1
    encoded.extend(_encode_run_length_repetition(count, previous))
    return bytes(encoded)


def _byte_encode(row: Sequence[int]) -> bytes:
    encoded = bytearray()
    for chunk_start in range(0, len(row), 8):
        value = 0
        for bit_index, bit in enumerate(row[chunk_start : chunk_start + 8]):
            if bit:
                value |= 1 << bit_index
        encoded.append(value)
    return bytes(encoded)


def cmd_print_row(row: Sequence[int], *, width_px: int | None = None) -> bytes:
    if width_px is None:
        width_px = len(row)
    if len(row) != width_px:
        raise ValueError(f"row width must be {width_px} pixels, got {len(row)}")

    encoded = _run_length_encode(row)
    if len(encoded) <= ceil(width_px / 8):
        return _command(0xBF, encoded)

    encoded = _byte_encode(row)
    return _command(0xA2, encoded)


def build_image_job(
    rows: Sequence[Sequence[int]],
    *,
    energy: int = 0xFFFF,
    feed_lines: int = 25,
    width_px: int | None = None,
    set_paper_repeats: int = 3,
) -> bytes:
    if not rows:
        raise ValueError("image job must contain at least one row")
    if width_px is None:
        width_px = len(rows[0])

    data = bytearray()
    data += CMD_GET_DEV_STATE
    data += CMD_SET_QUALITY_200_DPI
    data += cmd_set_energy(energy)
    data += cmd_apply_energy()
    data += CMD_LATTICE_START
    for row in rows:
        data += cmd_print_row(row, width_px=width_px)
    data += cmd_feed_paper(feed_lines)
    for _ in range(set_paper_repeats):
        data += CMD_SET_PAPER
    data += CMD_LATTICE_END
    data += CMD_GET_DEV_STATE
    return bytes(data)
