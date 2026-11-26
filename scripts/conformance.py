"""Check a running scope service against the spec. Usage: python -m scripts.conformance BASE_URL."""
import json
import sys
import urllib.request

from dwarf_relay.spec import BASE_OPERATIONS, CAPABILITY_OPERATIONS, SPEC_VERSION


def check(get_json) -> list[str]:
    """Return a list of problems. get_json maps a path to parsed JSON."""
    problems = []
    caps = get_json("/api/capabilities")
    if caps.get("spec_version", "").split(".")[0] != SPEC_VERSION.split(".")[0]:
        problems.append(f"spec_version {caps.get('spec_version')!r} is not major {SPEC_VERSION.split('.')[0]}")
    listed = {op["operationId"] for methods in get_json("/openapi.json")["paths"].values() for op in methods.values()}
    required = list(BASE_OPERATIONS)
    for name in caps.get("capabilities", []):
        if name not in CAPABILITY_OPERATIONS:
            problems.append(f"unknown capability {name!r}")
            continue
        required += CAPABILITY_OPERATIONS[name]
    problems += [f"missing operation {op}" for op in required if op not in listed]
    return problems


def main() -> int:
    base = sys.argv[1].rstrip("/")

    def get_json(path):
        with urllib.request.urlopen(base + path, timeout=20) as r:
            return json.load(r)

    problems = check(get_json)
    print("\n".join(problems) or "conforms")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
