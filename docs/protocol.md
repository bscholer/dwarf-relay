# DWARF 3 protocol notes

Results from live tests on firmware 1.5.4, on 2026-11-05. Message formats come from the proto files in [dwarf_python_api](https://github.com/stevejcl/dwarf_python_api). The library was read, not run.

## Ports

| Port | Use |
|---|---|
| 21 | FTP, anonymous. Album root has `Astronomy`, `Normal_Photos`, `Panoramas`, `Videos` |
| 80 | Album files over HTTP |
| 554 | RTSP |
| 8082 | HTTP JSON API |
| 9900 | WebSocket control, protobuf `WsPacket` |

## WebSocket packet

`WsPacket` fields: major 1, minor 7, device_id 2, module, cmd, type, data, client_id. Type 0 is a request, 2 is a notification, and 3 is a reply. A random client ID works.

The scope accepts one client. It refuses others with close code `4409 DEVICE_OCCUPIED`. The refusal continued for over 100 s after the app went to the background. It cleared when the phone left Wi-Fi.

## Motors

- Module 6. Reset 14003, run to 14001, position 14011.
- Position reads `-14520` (need reset) after a restart. Reset rotation, then pitch.
- Closed pitch: run to 0. The barrel reaches about 2, then returns to about 12.5 within seconds. The official app shows the same bounce.
- Pitch below 0 returns `-14505`, with no motion.
- Straight up on a tripod is about pitch 172.5, set by eye.

## Darks

- Tele start 11021, wide start 11025, module 3. Fields: exp_index, gain_index, bin_index, cap_size.
- Indexes: exposure 15 s = 156, 30 s = 159, 60 s = 162. Gain 60 = 18.
- List: 11023 tele, 11027 wide.
- The tele start sent no reply in 15 s. Files appear in `Astronomy/DWARF_DARK`, and the master appears in `Astronomy/CALI_FRAME/dark/cam_0`.
- Darks match by temperature, in the file name.
- A closed barrel test at 45 C showed a 14-count gradient. Heat or room light can cause this.

## Capture

Order: set time, time zone, location, then go live 11010, mode 16402 = 2, enter camera 16404, tech 16403 = 2.

- Exposure 16700, gain 16701, filter 16703 with `0x020100000000000d`. Filter values: 0 VIS, 1 Astro, 2 Duo-Band.
- Start 11005 with ir_index and force_start.
- Capture start resets `stackCount` to 999, so count frames from the FTP listing and send stop 11006.
- With no goto, 11005 returned `-11513`, but the capture ran. Read the state after any start reply.
- At that start, the scope moved rotation and pitch by itself. The cause is not known. `autoCalibration` is true on this scope.
- Binning parameter `0x020100000000001e` re-initialized the wide camera, and wide exposure and gain then read 0. The CLI does not send this parameter.

## Calibration

11000, module 3. Failure code `-11504` arrives after about 80 s. The frames that failed go to `Astronomy/Solving_Failed`. The scope needs stars in view.
