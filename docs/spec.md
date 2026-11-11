# Scope service spec v0.1

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
| `goto` | `gotoTarget` |
| `capture` | `startCapture`, `stopCapture` |
| `darks` | `listDarks`, `startDark` |
| `files` | `listSessions`, `listFiles`, `readFileChunk` |
| `delete` | `deleteSession` |
| `preview` | `getFrame` |
| `pointing` | `pointScope` |

A service lists a capability only when it implements every operation in its row. `scripts/conformance.py` checks this against a running service.

## Units and names

- Coordinates: RA in hours and Dec in degrees, J2000.
- Exposure: seconds. Gain: the scope gain number. Filter: a name from the service capability data.
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
