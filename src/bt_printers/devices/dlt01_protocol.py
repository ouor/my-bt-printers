from __future__ import annotations

from collections.abc import Sequence

LABEL_WIDTH_PX = 96
LABEL_LENGTH_PX = 320
LABEL_WIDTH_MM = 12
LABEL_LENGTH_MM = 40
LABEL_ROW_BYTES = LABEL_WIDTH_PX // 8
PACKET_ROW_BYTES = 96
ROWS_PER_PACKET = 8

STATUS = b"\x5a\x02"
HANDSHAKE_0A = b"\x5a\x0a"
HANDSHAKE_0B = b"\x5a\x0b"
PRINTING_PAUSED = b"\x5a\x08"
PRINTING_FINISHED = b"\x5a\x06"
LOST_PACKET = b"\x5a\x05"
STATIC_CHALLENGE = b"\x00" * 10


def hardware_info() -> bytes:
    return b"\x5a\x01" + b"\x00" * 10


def density(level: int) -> bytes:
    # Every 5a command this firmware accepts is 12 bytes; a short 3-byte packet
    # corrupts the following command in the stream.
    return b"\x5a\x0c" + bytes((max(0, min(6, level)),)) + b"\x00" * 9


def energy_to_density(energy: int) -> int:
    if 0 <= energy <= 6:
        return energy
    if energy == 0xFFFF:
        return 5
    return max(0, min(6, round((energy / 0xFFFF) * 6)))


def random_0a() -> bytes:
    return HANDSHAKE_0A + STATIC_CHALLENGE


def _crc16_xmodem(data: bytes) -> int:
    crc = 0
    for byte in data:
        for bit_index in range(8):
            bit = (byte >> (7 - bit_index)) & 1
            c15 = (crc >> 15) & 1
            crc = (crc << 1) & 0xFFFF
            if c15 ^ bit:
                crc ^= 0x1021
    return crc


def reply_0b(address: str) -> bytes:
    mac = bytes.fromhex(address.replace(":", ""))
    response = (_crc16_xmodem(STATIC_CHALLENGE[:1] + mac) >> 8) & 0xFF
    return HANDSHAKE_0B + bytes((response,)) * 10


def print_prepare() -> bytes:
    return b"\x5a\x03\x82\x00\x03\x00\x00\x00\x00\x00\x00\x00"


def print_status(
    num_lines: int,
    *,
    end: bool = False,
) -> bytes:
    # FunnyPrint reference driver print_event: 5a04 + line count (big-endian) +
    # end flag (little-endian uint16): 0x0000 = start, 0x0001 = finish.
    # Padded to the 12-byte control-frame size this printer's stream parser uses.
    flag = (1 if end else 0).to_bytes(2, "little")
    return b"\x5a\x04" + num_lines.to_bytes(2, "big") + flag + b"\x00" * 6


def print_status_sequence(line_count: int) -> tuple[bytes, ...]:
    # The reference driver sends a single start event carrying the job line count.
    return (print_status(line_count, end=False),)


def print_event(num_lines: int, *, end: bool = False) -> bytes:
    return print_status(num_lines=num_lines, end=end)


def _pack_row(row: Sequence[int], *, width_px: int = LABEL_WIDTH_PX) -> bytes:
    if len(row) != width_px:
        raise ValueError(f"row width must be {width_px}px, got {len(row)}px")

    packed = bytearray(LABEL_ROW_BYTES)
    for index, value in enumerate(row):
        if value:
            packed[index // 8] |= 1 << (7 - (index % 8))
    return bytes(packed)


def label_lines(rows: Sequence[Sequence[int]]) -> list[bytes]:
    if not rows:
        raise ValueError("print job must contain at least one row")

    width_px = len(rows[0])
    if width_px != LABEL_WIDTH_PX:
        raise ValueError(f"DL-T01 rows must be {LABEL_WIDTH_PX}px wide")

    lines: list[bytes] = []
    for offset in range(0, len(rows), ROWS_PER_PACKET):
        packet = bytearray(PACKET_ROW_BYTES)
        for row_index, row in enumerate(rows[offset : offset + ROWS_PER_PACKET]):
            row_bytes = _pack_row(row, width_px=width_px)
            start = row_index * LABEL_ROW_BYTES
            packet[start : start + LABEL_ROW_BYTES] = row_bytes
        lines.append(bytes(packet))
    return lines


def print_line(line_no: int, data: bytes) -> bytes:
    if len(data) != PACKET_ROW_BYTES:
        raise ValueError(f"DL-T01 print lines must be {PACKET_ROW_BYTES} bytes")
    return b"\x55" + line_no.to_bytes(2, "big") + data + b"\x00"


def build_print_packets(rows: Sequence[Sequence[int]], *, energy: int) -> list[bytes]:
    lines = label_lines(rows)
    line_count = len(lines)
    packets = [
        density(energy_to_density(energy)),
        print_prepare(),
    ]
    packets.extend(print_status_sequence(line_count))
    packets.extend(print_line(index, line) for index, line in enumerate(lines))
    packets.append(print_status(line_count, end=True))
    return packets
