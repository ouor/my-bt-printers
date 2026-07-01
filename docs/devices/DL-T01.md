# DL-T01

This document records the implementation decisions and first test result for
the DOLEWA DL-T01 12mm x 40mm Bluetooth label printer.

## Working configuration (resolved)

The DL-T01 now prints correctly. Root cause and final settings:

- **Print-event flag was wrong (the blocker).** `print_status` start events were
  built as `5a 04 {count} 00 01 2a ...`. The FunnyPrint reference driver encodes
  the event as `5a 04 {count_be} {end_flag_le16}`, so the start flag must be
  `00 00` (not `00 01 2a`) and finish is `01 00`. With the wrong flag the printer
  accepted the job and fed the label but never engaged the head (blank output).
  Fixed to the reference encoding; a single start event is sent with the job line
  count.
- **All `5a` control packets must be 12 bytes.** The start event was 13 bytes and
  `density` was 3 bytes; this firmware parses a 12-byte control-frame stream, so a
  wrong length desynced everything after it. Both are now padded to 12 bytes.
- **Send order:** hardware_info -> density -> print_prepare -> single start event
  -> `0x55` raster lines -> wait for `5a06` finished -> end event. No `5a0a`/`5a0b`
  handshake is performed.
- **Long-axis alignment:** the printer starts the burn ~2mm early and clips the
  output/leading edge. `DLT01Calibrate.long_axis_offset_mm` defaults to `0.5`
  (shift content toward the feed side), which lands content with a clean ~1.5mm
  margin at each 40mm-axis end. The response is non-linear: beyond ~0.5mm the
  printer triggers a label-gap advance (~8mm jump), so 0.5mm is the usable
  correction and the outer ~1.5mm of the long axis is an unusable safe zone.
- **Density:** tested at level 6 (`--energy 6`). Lower levels print fainter.
- The `0x55` raster line trailing byte is a terminator, not a checksum.

## Current implementation

- Device module: `src/bt_printers/devices/dlt01.py`
- Protocol module: `src/bt_printers/devices/dlt01_protocol.py`
- Registry aliases: `dlt01`, `dl-t01`, `dolewa-t01`, `label`
- Transport: Bluetooth LE through `bleak`
- Send path: hardware-info probe, density command, print event, and line packets
- Calibration script: `script/dlt01_calibration_pattern.py`

## Device class

This printer is a label maker, not a receipt/photo roll printer. The tested
label stock is `12mm x 40mm`.

Published/manual specifications list the model as `DL-T01`, max resolution as
`203dpi`, and max paper width as `15mm`. For the installed 12mm label, the code
uses the usual 203 DPI approximation of `8 dots/mm`:

- Physical label width across the printhead: `12mm`
- Physical label length along feed: `40mm`
- Print bitmap: `96 x 320 px`
- User-facing design canvas before rotation: `320 x 96 px`

Text and landscape images are prepared on the `320 x 96 px` label canvas, then
rotated into the printer transport orientation as `96 x 320 px`.

For the tested stock, the physical long-axis direction maps as follows:

- Physical right side: output/leading edge, first out of the printer
- Physical left side: feed/trailing edge, still inside the printer
- Transport `y=0`: physical right/output side
- Transport `y=319`: physical left/feed side

The first full-border label test showed the left border clipped and about
`6mm` of blank area on the right. A later attempted `6mm` lead-in was wrong: it
made the printer consume two labels and increased the blank area to about
`12mm`. DL-T01 calibration must not extend the raster beyond one physical label.
The transport page is therefore forced to exactly `96 x 320 px`.

The implementation exposes DL-T01-specific long-axis calibration fields instead
of reusing receipt-printer left/right margin terms:

- `output_edge_mm`: physical right/output side, transport `y=0`
- `feed_edge_mm`: physical left/feed side, transport `y=319`

Both values are applied inside the fixed `96 x 320 px` transport image. They
never increase the row count beyond one 40mm label.

Current defaults:

- `output_edge_mm=0.0`
- `feed_edge_mm=0.0`

These defaults target the full `12mm x 40mm` label area. Earlier feed-side
inset experiments are recorded below, but they are not treated as the final
full-label implementation.

## BLE profile

Observed device:

- BLE name: `DL-T01`
- Address used in testing: `8B:00:00:00:03:F7`
- MTU: `128`

Primary BLE service:

- `0000ffe6-0000-1000-8000-00805f9b34fb`

Primary BLE characteristics:

- Write characteristic:
  - `0000ffe1-0000-1000-8000-00805f9b34fb`
  - Properties: `write-without-response`
  - Descriptor text: `Commond`
- Notify characteristic:
  - `0000ffe2-0000-1000-8000-00805f9b34fb`
  - Properties: `notify`
  - Descriptor text: `Response`

Additional observed service:

- `5833ff01-9b8b-5191-6142-22a4536ef123`
  - Write: `5833ff02-9b8b-5191-6142-22a4536ef123`
  - Notify: `5833ff03-9b8b-5191-6142-22a4536ef123`

The current print path uses the `ffe6` service because it matches the
Xiqi/DOLEWA FunnyPrint reference implementation.

## Protocol

The command family is the Xiqi/DOLEWA `5a` protocol:

- Hardware info:
  - `5a 01 00 00 00 00 00 00 00 00 00 00`
- Legacy handshake helpers:
  - `5a 0a` + 10 static zero bytes
  - `5a 0b` + 10 bytes derived from CRC16-XMODEM over the static challenge
    prefix and printer BLE address
  - These helpers remain in `dlt01_protocol.py` for reference, but the current
    print path does not send them.
- Density:
  - `5a 0c {level}`
- Print start/end event:
  - Start: `5a 04 {line_count_hi} {line_count_lo} 00 00 00 00`
  - End: `5a 04 {line_count_hi} {line_count_lo} 01 00 00 00`
- Raster line:
  - `55 {line_no_hi} {line_no_lo} {96 bytes} 00`

The reference roll-printer protocol packs two 384-dot rows into each 96-byte
line packet. DL-T01 uses a 12mm / 96-dot physical label width in this project,
so each physical raster row is only `12 bytes`. Therefore each 96-byte line
packet carries eight physical label rows.

This distinction matters. An earlier implementation incorrectly packed only two
12-byte rows into each 96-byte packet and left the rest white. The printer then
stretched the label in the feed direction and consumed multiple labels.

The DOLEWA app's DL-T01 path was later inspected from its Hermes bytecode. That
path may differ from the generic FunnyPrint roll-printer path in two important
ways, but the current project keeps the packet shape that printed successfully
on the tested device:

- `5a 04` print status packets are allocated as `8` bytes, leaving two trailing
  zero bytes after the start/end flag.
- Some app paths appear to use command byte `0x56` for device names starting
  with `DL-T01`; this project currently uses `0x55`.

The app also converts rows from a 384-dot intermediate image by taking the last
`12` bytes of each 48-byte row, then groups those 12-byte rows into 96-byte
line packets. That matches the project's current `8 x 12-byte row` grouping.

An attempted start event using the label dimensions as data bytes
(`28 0c`, meaning `40, 12`) was rejected after testing: it consumed three
labels from a single 320-row job. A later attempt to use `01 00` as the start
event without a separate end event produced no print at all. The implementation
therefore keeps the last event shape that actually printed: `00 00` to start
and `01 00` to finish.

The current send order is:

1. Hardware-info probe, then wait briefly for the first reply.
2. Density command.
3. Print-prepare command.
4. Start event.
5. Exactly 40 raster line packets.
6. Wait for the printer's finish notification, or for `ready_timeout` if no
   event arrives after the final raster packet.
7. End event.

Any `5a 06` notification received before all 40 raster line packets have been
sent is ignored. Treating an early `5a 06` as completion can truncate the job
and produce feed-side clipping no matter how much blank area is added inside
the image.

The DOLEWA app path is still ambiguous at the decompiled call-site level. On
the tested printer, omitting the end event made the job transfer complete
without any visible print, so the implementation keeps the explicit end event.

## Legacy handshake note

The reference driver treats a second `5a 0b` notification with payload byte
`0x01` as handshake success. The current implementation does not perform this
handshake, but the helper functions are kept for reference while the protocol
notes remain in flux.

The tested DL-T01 firmware returned the exact `5a 0b` response payload sent by
the host, for example:

- `5a 0b dd dd dd dd dd dd dd dd dd dd`

The old handshake experiment accepted either form:

- `result[2] == 0x01`
- response payload echoed back exactly

## Density

The reference CUPS driver uses density level `3` as Normal. The first DL-T01
test used that value and the text printed very faintly. The DL-T01
implementation now maps the generic CLI default `--energy 0xffff` to density
`5`.

For direct control, pass `--energy 0` through `--energy 6`; those values are
used as protocol density levels directly.

## First test output

Command:

```powershell
.\.venv\Scripts\python.exe -m bt_printers --printer dlt01 print-text "DL-T01 OK" --device DL-T01 --align center --font-size 28
```

Prepared data:

- Image size: `96 x 320 px`
- Physical raster rows: `320`
- Protocol line packets: `40`
- Max packet size: `100 bytes`
- Print summary: `width=96 rows=320 bytes=4019`

The host completed the first hardware-info probe and data transfer successfully, but
the first printed label showed the row-packing issue described above and text
density was too light. The fixed row packing and density `5` default should be
verified with the smallest possible next print.

After the border test, a `6mm` lead-in was tried and rejected. It produced two
labels and shifted the blank area further in the wrong direction. A normal
12mm x 40mm label image must remain one label long:

- Visible content: `96 x 320 px`
- Transport image after calibration: `96 x 320 px`
- Protocol line packets: `40`
- Print summary: `width=96 rows=320 bytes=4019`

A later attempt to use a `15mm` virtual head width made the printer feed one
label but print nothing. The `ffe6` protocol expects `96` data bytes per line
packet on this device, so that experiment was rejected too.

An attempted start event of `5a 04 00 28 28 0c 00 00` was also rejected. It
printed three labels: the first still had the left edge clipped and about
`3.5mm` blank on the right, the second had about `1mm` blank on the right, and
the third was cut off mid-print. This indicates those bytes affect feed state
or label sequencing, not a safe paper-size declaration.

Long-axis border calibration tests:

- Full one-label border with the safe `00 00` start event printed one label,
  but the physical left/feed side was clipped and the physical right/output
  side had about `3.5mm` blank area.
- Adding a `28px` blank inset at transport `y=0` printed one label, but kept
  the left/feed side clipped and increased the right/output blank area to about
  `10mm`. This confirms `y=0` maps to the physical right/output side.
- Adding a `28px` blank inset at transport `y=319` printed one label and left
  about `6.5mm` blank area on the right/output side. Further calibration should
  keep protocol events unchanged and adjust only the one-label image geometry.
- Sending the end event immediately after the final line packet also produced
  feed-side clipping and about `6.5mm` output-side blank area. This timing was
  rejected.
- A delayed-end timing plus a `3.5mm` feed-side inset still produced feed-side
  clipping and about `6.5mm` output-side blank area.
- A delayed-end timing plus a `6.5mm` feed-side inset still placed the
  feed-side border too close to the physical cut edge, so the border remained
  clipped.
- A delayed-end timing plus an `8.0mm` feed-side inset produced a safe visible
  feed-side margin, but it also conceded that the full label was not being
  covered. That is kept as a calibration observation, not as the final default.
- A full-label attempt using `01 00` as the start event and no extra end event
  produced no visible print, so that path was rejected.
- The current full-label implementation uses no feed-side inset, start
  `00 00`, and end `01 00`.
- If feed-side clipping persists even with a `6.5mm` feed-side inset, verify
  first that all 40 line packets are sent. The implementation now ignores early
  `5a 06` notifications until the full raster has been transmitted.

Current calibration helper:

```powershell
.\.venv\Scripts\python.exe script\dlt01_calibration_pattern.py
.\.venv\Scripts\python.exe script\dlt01_calibration_pattern.py --print
```

The first command is a dry run. The second command sends one asymmetric
calibration label using the safe one-label protocol shape.

## Known caveats

- Actual label alignment and printable offset still need paper measurements
  from the printed label. The current remaining offset is along the 40mm label
  direction.
- The current implementation assumes 12mm x 40mm stock. Other DL-T01 media
  sizes should get explicit calibration before use.
- The `5833ff01-*` service is not used by the current print path.
- Because DL-T01 advertisements may not include service UUIDs, printing without
  `--device DL-T01` may be less reliable than passing the name or address.
