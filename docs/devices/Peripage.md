# PeriPage A6+

This document records the implementation decisions and calibration history for
the PeriPage A6+ BLE printer tested as `PeriPage+8B91_BLE`.

## Current implementation

- Device module: `src/bt_printers/devices/peripage.py`
- Protocol module: `src/bt_printers/devices/peripage_protocol.py`
- Registry aliases: `peripage`, `peripage-a6p`
- Transport: Bluetooth LE through `bleak`
- Send path: protocol packet sequence through `send_packet_sequence()`

The reference project in `ref/peripage-python` uses a classic Bluetooth /
RFCOMM transport, but the same image command bytes work over this printer's BLE
write characteristic when command packet boundaries are preserved.

## BLE profile

Observed device:

- BLE name: `PeriPage+8B91_BLE`
- Address used in testing: `45:54:07:05:8B:91`
- Firmware reported by query: `V1.12_304dpi`
- Hardware reported by query: `v3.38.21_AY`
- Battery reported by query: `100`

Advertised / exposed services:

- `0000fee7-0000-1000-8000-00805f9b34fb`
- `0000ff00-0000-1000-8000-00805f9b34fb`
- `49535343-fe7d-4ae5-8fa9-9fafd205e455`

Primary BLE characteristics used:

- Write characteristic:
  - `0000ff02-0000-1000-8000-00805f9b34fb`
- Notify characteristic:
  - `0000ff01-0000-1000-8000-00805f9b34fb`

Other exposed characteristics:

- `0000ff03-0000-1000-8000-00805f9b34fb` notify
- `49535343-8841-43f4-a8d4-ecbe34729bb3` write
- `49535343-1e4d-4bd9-ba61-23c647249616` notify

Effective print width:

- `576 px`
- `72 bytes` per raster row

## Prepare

The PeriPage implementation uses the shared preparation helpers:

- `rasterize_text()` for text-to-bitmap rendering.
- `resize_image_to_width()` for image scaling.
- `image_to_rows()` for threshold or Floyd-Steinberg raster conversion.

PeriPage image output enables automatic average-density limiting for
Floyd-Steinberg output:

- `PERIPAGE_MAX_AVERAGE_DENSITY=0.42`

This was introduced because the first successful image print was too dark. A
limit of `0.30` was then too light, and `0.42` was judged appropriate on the
tested paper and printer. The density calculation trims blank edges before
measuring so trailing feed does not affect image tone.

## Calibrate

Current calibration in `PERIPAGE_A6P_CALIBRATION`:

- `paper_width_mm=48.5`
- `left_margin_mm=0.0`
- `right_margin_mm=0.0`
- `top_margin_mm=0.0`
- `bottom_margin_mm=12.5`
- `dots_per_mm_x=576 / 48.5`
- `dots_per_mm_y=576 / 48.5`
- `feed_lines=0`
- `set_paper_repeats=0`

The top margin is left at `0 mm` in software because the printer produced about
`6 mm` of physical top margin by itself. The bottom software margin is
`12.5 mm` because about `6.5 mm` of trailing paper remained inside the printer
body. Adding `12.5 mm` produced an observed final bottom margin of about
`6 mm`, matching the top.

## Print protocol

The PeriPage image command sequence is:

1. Reset command:
   - `10 ff fe 01 00 00 00 00 00 00 00 00 00 00 00 00`
2. Concentration command:
   - `10 ff 10 00 {level}`
3. Per image chunk:
   - Reset command.
   - Image header: `1d 76 30 00 {row_bytes} 00 {chunk_height} 00`
   - Packed row bytes.

The concentration level is derived from the public `energy` argument:

- `< 0x5555` -> level `0`
- `< 0xAAAA` -> level `1`
- otherwise -> level `2`

Rows are packed MSB-first, eight pixels per byte. A truthy row value means a
black dot.

## Important transport note

PeriPage did not print when the full image job was concatenated and then split
only by BLE MTU. The printer accepted the connection and the host completed the
write, but the device produced no paper output.

The working transport preserves protocol packet boundaries:

- reset as one BLE write
- concentration as one BLE write
- chunk header as one BLE write
- each 72-byte raster row as one BLE write

This keeps every PeriPage protocol packet smaller than the observed BLE MTU and
avoids splitting a command across arbitrary BLE chunks.

## Calibration observations

Initial successful image output:

- Width: `576 px`
- Rows for `test/image.jpg`: `623`
- Bytes sent before trailing feed: `44949`
- Output printed but was too dark.

Density experiments:

- Original image average black-dot ratio: about `0.600`
- Density limit `0.30`: too light
- Density limit `0.42`: judged appropriate

Trailing-feed experiments:

- With no software bottom feed, about `6.5 mm` of the bottom remained inside the
  printer body.
- With `bottom_margin_mm=6.5`, the final observed bottom margin was about
  `0 mm`.
- With `bottom_margin_mm=12.5`, the final observed bottom margin matched the
  top margin at about `6 mm`.

Current expected output for `test/image.jpg`:

- Content size: `576 x 623 px`
- Page size after calibration: `576 x 771 px`
- Bottom blank rows: `148 px`
- Image-area black-dot ratio: about `0.420`
- Print summary: `width=576 rows=771 bytes=55629`

## Known caveats

- The current PeriPage profile is tuned for the tested A6+ 304 DPI BLE device.
- The `49535343-*` service was observed but is not used by the current print
  path.
- If a future PeriPage model ignores prints, first check whether the BLE write
  characteristic or packet-boundary requirement differs.
- If paper or battery changes make photos too light or too dark, tune
  `PERIPAGE_MAX_AVERAGE_DENSITY` before changing protocol concentration.
