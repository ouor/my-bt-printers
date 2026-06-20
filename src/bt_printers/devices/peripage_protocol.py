from __future__ import annotations

from collections.abc import Sequence

RESET = bytes.fromhex("10fffe01000000000000000000000000")


def concentration_command(level: int) -> bytes:
    level = max(0, min(2, level))
    return bytes.fromhex("10ff1000") + bytes((level,))


def energy_to_concentration(energy: int) -> int:
    if energy < 0x5555:
        return 0
    if energy < 0xAAAA:
        return 1
    return 2


def pack_row(row: Sequence[int], *, row_width_px: int, row_bytes: int) -> bytes:
    if len(row) != row_width_px:
        raise ValueError(f"row width must be {row_width_px}px, got {len(row)}px")

    packed = bytearray(row_bytes)
    for index, value in enumerate(row):
        if value:
            packed[index // 8] |= 1 << (7 - (index % 8))
    return bytes(packed)


def build_image_job(
    rows: Sequence[Sequence[int]],
    *,
    row_width_px: int,
    row_bytes: int,
    energy: int,
    chunk_height: int = 0xFF,
) -> bytes:
    if not rows:
        raise ValueError("image job must contain at least one row")
    if not 1 <= chunk_height <= 0xFF:
        raise ValueError("chunk_height must be between 1 and 255")

    packed_rows = [
        pack_row(row, row_width_px=row_width_px, row_bytes=row_bytes) for row in rows
    ]

    data = bytearray()
    data += RESET
    data += concentration_command(energy_to_concentration(energy))

    for offset in range(0, len(packed_rows), chunk_height):
        chunk = packed_rows[offset : offset + chunk_height]
        data += RESET
        data += bytes.fromhex("1d763000")
        data += bytes((row_bytes, 0x00, len(chunk), 0x00))
        for row in chunk:
            data += row

    return bytes(data)


def build_image_packets(
    rows: Sequence[Sequence[int]],
    *,
    row_width_px: int,
    row_bytes: int,
    energy: int,
    chunk_height: int = 0xFF,
) -> list[bytes]:
    if not rows:
        raise ValueError("image job must contain at least one row")
    if not 1 <= chunk_height <= 0xFF:
        raise ValueError("chunk_height must be between 1 and 255")

    packed_rows = [
        pack_row(row, row_width_px=row_width_px, row_bytes=row_bytes) for row in rows
    ]

    packets = [
        RESET,
        concentration_command(energy_to_concentration(energy)),
    ]
    for offset in range(0, len(packed_rows), chunk_height):
        chunk = packed_rows[offset : offset + chunk_height]
        packets.append(RESET)
        packets.append(
            bytes.fromhex("1d763000")
            + bytes((row_bytes, 0x00, len(chunk), 0x00))
        )
        packets.extend(chunk)
    return packets
