# dwarf3-control

Command-line control for a DWARF 3 smart telescope on the local network. Goal: keep the scope outside on power, set a target, capture darks, and copy files.

Tested on firmware 1.5.4. This client speaks the scope WebSocket protocol directly. See [docs/protocol.md](docs/protocol.md).

## Install

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

## Use

Close the DWARFLAB app first. The scope accepts one control client.

```sh
dwarf3 status
dwarf3 darks
dwarf3 dark --exposure 15 --frames 3
dwarf3 prepare
dwarf3 calibrate
dwarf3 goto "NGC 2174" 6.1617 20.5
dwarf3 capture --frames 5 --exposure 15 --gain 60 --filter duo
dwarf3 stop
dwarf3 close
dwarf3 mirror ./data/astronomy
```

Set `DWARF3_HOST` for each command. `prepare` also needs `DWARF3_LAT`, `DWARF3_LON`, `DWARF3_UTC_OFFSET`, and `DWARF3_TZ`.

## Relay

`dwarf3 proxy` lets the DWARFLAB app and local tools share the one control connection. Enter the relay IP in the app. The app shows the relay as `DWARF3_RELAY1`.

```sh
dwarf3 proxy --log-dir data/proxy
dwarf3 nudge --angle 0 --length 0.8 --seconds 2
```

The relay logs decoded traffic as JSONL. It redacts passwords from API replies, but UDP replies hold the MAC and serial number. Do not share these logs.

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
