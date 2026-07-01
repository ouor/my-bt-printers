from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEVICE_DIR = ROOT / "src" / "bt_printers" / "devices"


def load_protocol(name: str):
    spec = importlib.util.spec_from_file_location(
        f"{name}_protocol_under_test",
        DEVICE_DIR / f"{name}_protocol.py",
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load protocol module {name!r}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mx10 = load_protocol("mx10")
peripage = load_protocol("peripage")
dlt01 = load_protocol("dlt01")


class MX10ProtocolTests(unittest.TestCase):
    def test_energy_command_uses_big_endian_payload(self) -> None:
        packet = mx10.cmd_set_energy(0x1234)

        self.assertEqual(packet[:6], bytes.fromhex("51 78 af 00 02 00"))
        self.assertEqual(packet[6:8], bytes.fromhex("12 34"))
        self.assertEqual(packet[-1], 0xFF)

    def test_energy_command_rejects_out_of_range_values(self) -> None:
        with self.assertRaises(ValueError):
            mx10.cmd_set_energy(-1)
        with self.assertRaises(ValueError):
            mx10.cmd_set_energy(0x10000)

    def test_print_row_uses_rle_when_shorter_than_packed_bytes(self) -> None:
        packet = mx10.cmd_print_row([0] * mx10.PRINT_WIDTH)

        self.assertEqual(packet[:3], bytes.fromhex("51 78 bf"))
        self.assertEqual(packet[4], 4)
        self.assertEqual(packet[6:10], bytes.fromhex("7f 7f 7f 03"))

    def test_print_row_falls_back_to_packed_bytes_for_noisy_rows(self) -> None:
        row = [index % 2 for index in range(mx10.PRINT_WIDTH)]
        packet = mx10.cmd_print_row(row)

        self.assertEqual(packet[:3], bytes.fromhex("51 78 a2"))
        self.assertEqual(packet[4], mx10.PRINT_WIDTH // 8)


class PeriPageProtocolTests(unittest.TestCase):
    def test_pack_row_sets_most_significant_bit_first(self) -> None:
        row = [1, 0, 0, 0, 0, 0, 0, 1]

        self.assertEqual(
            peripage.pack_row(row, row_width_px=8, row_bytes=1),
            bytes.fromhex("81"),
        )

    def test_pack_row_rejects_wrong_width(self) -> None:
        with self.assertRaises(ValueError):
            peripage.pack_row([1, 0, 1], row_width_px=8, row_bytes=1)

    def test_build_image_packets_preserves_command_boundaries(self) -> None:
        rows = [[1, 0, 0, 0, 0, 0, 0, 1], [0] * 8]
        packets = peripage.build_image_packets(
            rows,
            row_width_px=8,
            row_bytes=1,
            energy=0,
            chunk_height=2,
        )

        self.assertEqual(packets[0], peripage.RESET)
        self.assertEqual(packets[1], bytes.fromhex("10 ff 10 00 00"))
        self.assertEqual(packets[2], peripage.RESET)
        self.assertEqual(packets[3], bytes.fromhex("1d 76 30 00 01 00 02 00"))
        self.assertEqual(packets[4:], [bytes.fromhex("81"), bytes.fromhex("00")])


class DLT01ProtocolTests(unittest.TestCase):
    def test_density_packets_are_fixed_length_and_clamped(self) -> None:
        self.assertEqual(dlt01.density(9), b"\x5a\x0c\x06" + b"\x00" * 9)
        self.assertEqual(len(dlt01.density(3)), 12)

    def test_print_status_encodes_line_count_and_end_flag(self) -> None:
        self.assertEqual(
            dlt01.print_status(40, end=False),
            b"\x5a\x04\x00\x28\x00\x00" + b"\x00" * 6,
        )
        self.assertEqual(
            dlt01.print_status(40, end=True),
            b"\x5a\x04\x00\x28\x01\x00" + b"\x00" * 6,
        )

    def test_label_lines_group_eight_physical_rows_per_packet(self) -> None:
        rows = [[1] * dlt01.LABEL_WIDTH_PX for _ in range(16)]
        lines = dlt01.label_lines(rows)

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], b"\xff" * dlt01.PACKET_ROW_BYTES)
        self.assertEqual(lines[1], b"\xff" * dlt01.PACKET_ROW_BYTES)

    def test_print_line_wraps_line_number_and_payload(self) -> None:
        payload = bytes(range(dlt01.PACKET_ROW_BYTES))

        self.assertEqual(
            dlt01.print_line(3, payload),
            b"\x55\x00\x03" + payload + b"\x00",
        )

    def test_label_lines_reject_wrong_width(self) -> None:
        with self.assertRaises(ValueError):
            dlt01.label_lines([[0] * (dlt01.LABEL_WIDTH_PX - 1)])


if __name__ == "__main__":
    unittest.main()
