# Scope service spec v0.2

A scope service controls one telescope for a client such as astrolab. The client has no scope-specific code. It reads the capabilities list and uses only the features the service reports.

The service publishes `/openapi.json` and an MCP endpoint at `/mcp`. Both come from the same declarations. The machine-readable copies are `docs/openapi.json` and `docs/mcp-tools.json`.

## Version

`GET /api/capabilities` returns `spec_version`. A client refuses a service with a different major version. A minor version only adds optional fields.

## Capabilities

| Capability | Operations (`operationId`) |
|---|---|
| (always) | `getCapabilities` |
| `status` | `getStatus` |
| `setup` | `prepareScope`, `calibrateScope` |
| `focus` | `autofocusScope` |
| `goto` | `gotoTarget` |
| `capture` | `startCapture`, `stopCapture` |
| `darks` | `listDarks`, `startDark` |
| `files` | `listSessions`, `listFiles`, `readFileChunk` |
| `delete` | `deleteSession` |
| `preview` | `getFrame` |
| `pointing` | `pointScope` |

A service lists a capability only when it implements every operation in its row. `scripts/conformance.py` checks this against a running service.

### Capture options (0.2)

Choices that depend on the scope model come in `capture`, next to the capability list:

```json
"capture": {"filters": ["vis", "astro", "duo"], "exposures": ["1", "2", "15", "30", "60"], "wide_darks": true}
```

`startCapture` takes a filter and an exposure from these lists, and `startDark` takes `wide=true` only when `wide_darks` is true. A 0.1 service has no `capture` field; a client then assumes vis, astro and duo, and 15, 30 and 60 seconds.

## Units and names

- Coordinates: RA in hours and Dec in degrees, J2000.
- Exposure: seconds, as a string from `capture.exposures`. Gain: the scope gain number. Filter: a name from `capture.filters`.
- Errors: HTTP status plus `{"detail": text, "code": code}`. MCP tools return `isError` with the text.

## Error codes

| Code | Status | Meaning |
|---|---|---|
| `invalid_request` | 422 | Body or query is wrong. |
| `sun_too_close` | 422 | Target is inside the Sun limit. |
| `busy` | 409 | Another job or client holds the scope. |
| `scope_error` | 502 | The scope failed or did not answer. |
| `unauthorized` | 401 | Missing or wrong bearer token. |

## Auth

A service may require `Authorization: Bearer <token>`. The OpenAPI file shows which operations need it.

## Not in v0.1

Job queue, pause and resume, file copy, events, weather. These are separate tickets in the project board. Each becomes a new capability with its own operation row.
