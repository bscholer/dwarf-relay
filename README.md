# dwarf-relay

A relay and command-line control for DWARF smart telescopes on the local network. Goal: keep the scope outside on power, set a target, capture darks, and copy files.

It's tested on a DWARF 3 (firmware 1.5.4). The DWARF mini and DWARF II should work too, but nobody has tried them yet (see [Models](#models)). It speaks the scope's WebSocket protocol directly, with message types and command numbers from [dwarf_python_api](https://github.com/stevejcl/dwarf_python_api). See [docs/protocol.md](docs/protocol.md).

This used to be called dwarf3-control.

## Models

The relay asks the scope which model it is (`POST :8082/deviceInfo`) and adjusts. Set `DWARF_MODEL` (`dwarf3`, `dwarfmini` or `dwarf2`) if detection gets it wrong.

| | DWARF 3 | DWARF mini | DWARF II |
|---|---|---|---|
| Tested with the relay | yes | no | no |
| Filters | vis, astro, duo | astro, duo | vis (IR cut), astro (IR pass) |
| Longest exposure | 120 s | 180 s | 15 s |
| Point up and close barrel | yes | skipped, angles unknown | skipped, angles unknown |
| Wide darks | yes | yes | no |

The mini and DWARF II values come from [dwarf_python_api](https://github.com/stevejcl/dwarf_python_api) and the device profiles in [dwarfAlp](https://github.com/acocalypso/dwarfAlp). `GET /api/capabilities` reports the detected model and its filters and exposures. If you run the relay with a mini or a DWARF II, please open an issue with what worked and what didn't. The motor angles for pointing straight up are the most useful thing to send.

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Use

Close the DWARFLAB app first. The scope accepts one control client.

```sh
dwarf-relay status
dwarf-relay darks
dwarf-relay dark --exposure 15 --frames 3
dwarf-relay prepare
dwarf-relay calibrate
dwarf-relay goto "NGC 2174" 6.1617 20.5
dwarf-relay capture --frames 5 --exposure 15 --gain 60 --filter duo
dwarf-relay stop
dwarf-relay close
dwarf-relay mirror ./data/astronomy
```

Set `DWARF3_HOST` for each command (the name predates the other models). `prepare` also needs `DWARF3_LAT`, `DWARF3_LON`, `DWARF3_UTC_OFFSET`, and `DWARF3_TZ`.

## Relay

`dwarf-relay proxy` lets the DWARFLAB app and local tools share the one control connection. Enter the relay IP in the app. The app shows the relay as `DWARF3_RELAY1`.

```sh
dwarf-relay proxy --log-dir data/proxy
dwarf-relay nudge --angle 0 --length 0.8 --seconds 2
```

The relay logs decoded traffic as JSONL. It redacts passwords from API replies, but UDP replies hold the MAC and serial number. Do not share these logs.

## MCP

The relay also serves MCP over HTTP at `http://<relay-ip>:9902/mcp`. Commands use the app connection if the app is connected. Control tools need a bearer token (see Auth). `status` and `capabilities` stay open.

Tools: `status`, `list_sessions`, `list_darks`, `prepare`, `move`, `close_barrel`, `calibrate`, `goto`, `start_capture`, `stop_job`, `start_dark`. `goto` refuses a target within 30 degrees of the Sun. Only `status`, `list_sessions`, `list_darks`, and `move` have a live test.

Each tool is also a REST route under `/api/` on the same port, such as `GET /api/status` and `POST /api/goto`. Both come from one declaration in `dwarf_relay/api.py` (`@http_endpoint` and `@mcp_tool` in `dwarf_relay/endpoints.py`). The published specs are `docs/openapi.json` and `docs/mcp-tools.json`, also live at `/openapi.json` and MCP `tools/list`. After you change a tool, run `python -m scripts.export_spec`. A test fails when the files are stale. Tools default to `authentication=AuthMode.TOKEN`. Only `status` and `capabilities` are open.

## Auth

Auth is off by default, like the scope itself, so anyone on your network can control the scope through the relay. To require a token, set `DWARF3_AUTH=on`, or just set `DWARF3_API_TOKEN`. Then every operation except `status` and `capabilities` needs `Authorization: Bearer <token>`, on REST and MCP alike. With `DWARF3_AUTH=on` and no `DWARF3_API_TOKEN`, the relay makes a token on first start and keeps it in `<log-dir>/api-token` (mode 600), then prints the path, not the token. `status` also reports battery, temperatures, SD card size, the capture, tracking, goto, and calibration state codes, and scope reachability, as the scope last sent them. A field is empty until the scope has sent it. Capture and tracking `1` mean running. `capabilities` reports the relay version.

### Live updates

`GET /api/events` is a server-sent event stream for anything that wants to follow the scope without polling. It sends one `status` event as soon as you connect, then another within a quarter of a second of any change in what the relay knows (battery, temperatures, capture, tracking, goto and calibration states, SD card, the app connection, a job, scope reachability). A comment line comes every 15 seconds while nothing changes, so a listener can tell a quiet scope from a dead relay. It sends the same fields as `/api/status`, except the dark counts, and it is open like `status`. It reads only what the relay already holds, so it opens no connection to the scope, and the relay logs nothing for it. Motor positions arrive when `status` last read them. `capabilities` lists `events` when the relay has it.

The relay checks every 2 seconds whether the scope is on while it is away (every 10 while it is on), so it notices the scope coming online within seconds.

## Metrics and logs

`GET :9902/metrics` serves Prometheus metrics: scope reachability, app connection, battery, SD space, temperatures, capture and calibration state, commands, errors, and traffic. Scope values update only while a client is connected. Stdout has one JSON line for each command or error, with no payloads.

## Status

| Function | Live test |
|---|---|
| `status`, `darks` | Works |
| `dark` tele | Protocol works in a test script |
| `prepare` | Protocol works in a test script |
| `mirror` | Copied one 16.8 MB frame. Stalled when the scope left the network, with no retry yet |
| `stop` | Works in a test script |
| `proxy` | App connects, previews, and controls the scope through it |
| `nudge` | Moves the scope while the app stays connected |
| `calibrate` | Ran. Failed under cloud with `-11504` |
| `capture` frame count | Not tested on the scope. Unit test only |
| `goto`, autofocus, `dark --wide` | Not tested |

## Next steps

1. Run `calibrate`, `goto`, and `capture` on a clear night.
2. Find why the scope moves motors at capture start with no goto.
3. Test how a stale client clears after a lost connection.
4. Add the scheduled service for darks and file mirror.

## Security

`POST :8082/deviceInfo` has no login and returns the home Wi-Fi password in clear text. Change the default scope AP password before the scope goes outside.

## Tests

```sh
.venv/bin/pytest
```
