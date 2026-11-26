"""What differs between DWARF models, and how to tell which one is connected.

Only the DWARF 3 has been tested with this relay. The DWARF II and mini values come from
dwarf_python_api (exposure tables, filter positions, /deviceInfo ids, album folders) and from
the device profiles in dwarfAlp (packet header). They should work, but nobody has run them yet.
Where a value is not known for a model (the motor angles that point the scope up or close
it), the relay skips that step and says so instead of guessing.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

log = logging.getLogger("dwarf_relay.models")

API_PORT = 8082


@dataclass(frozen=True)
class Model:
    key: str
    name: str
    device_id: int  # deviceId in the scope's /deviceInfo reply
    library_type: str  # dwarf_python_api's model number for its tables: "2" DWARF II, "3" DWARF 3, "5" mini
    header: tuple[int, int, int]  # WsPacket major_version, minor_version, device_id for the relay's own connection
    filters: dict[str, int]  # filter name -> IR filter index
    album_roots: tuple[str, ...]  # FTP folders that may hold the astro sessions, tried in order
    album_http_root: str  # the same folder as the scope's album API names it
    name_prefix: str  # start of the scope's device name, before the 6 hex digits
    tested: bool = False
    pitch_zenith: float | None = None
    pitch_closed: float | None = None
    rotation_center: float | None = None
    max_pitch: float = 175.0
    wide_astro: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)


DWARF3 = Model(
    key="dwarf3",
    name="DWARF 3",
    device_id=2,
    library_type="3",
    header=(1, 7, 2),  # what the DWARFLAB app sends, seen through the relay
    filters={"vis": 0, "astro": 1, "duo": 2},
    album_roots=("/Astronomy",),
    album_http_root="/DWARF3/Astronomy",
    name_prefix="DWARF3_",
    tested=True,
    pitch_zenith=172.5,  # set by eye on a tripod; the polar value 169 is about 3 degrees short
    pitch_closed=0.0,
    rotation_center=158.0,
)

DWARF_MINI = Model(
    key="dwarfmini",
    name="DWARF mini",
    device_id=4,
    library_type="5",
    header=(1, 20, 4),
    # Index 0 is the dark position on the mini; the app only uses it for darks.
    filters={"astro": 1, "duo": 2},
    album_roots=("/Astronomy", "/DWARF_mini/Astronomy"),
    album_http_root="/DWARF_mini/Astronomy",
    name_prefix="DWARF_mini_",
)

DWARF2 = Model(
    key="dwarf2",
    name="DWARF II",
    device_id=1,
    library_type="2",
    header=(1, 20, 4),
    # The DWARF II has no filter wheel, only an IR-cut switch: 0 cuts IR, 1 passes it.
    filters={"vis": 0, "astro": 1},
    album_roots=("/DWARF_II/Astronomy", "/Astronomy"),
    album_http_root="/DWARF_II/Astronomy",
    name_prefix="DWARF_",
    wide_astro=False,
)

MODELS = (DWARF3, DWARF_MINI, DWARF2)
DEFAULT = DWARF3


def by_key(key: str) -> Model:
    wanted = key.strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    aliases = {"dwarf3": DWARF3, "d3": DWARF3, "dwarfmini": DWARF_MINI, "mini": DWARF_MINI,
               "dwarf2": DWARF2, "dwarfii": DWARF2, "d2": DWARF2}
    if wanted not in aliases:
        raise ValueError(f"unknown model {key!r}, use one of {', '.join(m.key for m in MODELS)}")
    return aliases[wanted]


def from_device_info(info: dict) -> Model | None:
    """Pick the model from a /deviceInfo reply: deviceId first, then the device name."""
    device_id = info.get("deviceId")
    for model in MODELS:
        if device_id == model.device_id:
            return model
    name = str(info.get("deviceName") or "")
    # Longest prefix first, so DWARF_mini_ wins over DWARF_.
    for model in sorted(MODELS, key=lambda m: -len(m.name_prefix)):
        if name.startswith(model.name_prefix):
            return model
    return None


def device_info(host: str, urlopen=urllib.request.urlopen, timeout: float = 5) -> dict | None:
    request = urllib.request.Request(
        f"http://{host}:{API_PORT}/deviceInfo", data=b"{}", headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urlopen(request, timeout=timeout) as resp:
            return json.loads(resp.read()).get("data") or {}
    except (OSError, ValueError):
        return None


def detect(host: str, env=os.environ, urlopen=urllib.request.urlopen) -> Model | None:
    """The model set in DWARF_MODEL, else the one the scope reports. None when the scope does not answer."""
    if env.get("DWARF_MODEL"):
        return by_key(env["DWARF_MODEL"])
    info = device_info(host, urlopen)
    if info is None:
        return None
    model = from_device_info(info)
    if model is None:
        log.warning("unknown scope model (deviceId %s), treating it as a %s", info.get("deviceId"), DEFAULT.name)
        return DEFAULT
    if not model.tested:
        log.warning("%s detected. This relay is tested on the DWARF 3 only; please report what works.", model.name)
    return model


@cache
def _tables():
    """dwarf_python_api's data_utils.py, loaded on its own: the package's lib/__init__ pulls in its socket code."""
    spec = importlib.util.find_spec("dwarf_python_api")
    path = Path(spec.origin).parent / "lib" / "data_utils.py"
    module_spec = importlib.util.spec_from_file_location("dwarf_python_api_data_utils", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def exposure_index(model: Model, seconds: str) -> int:
    """The camera's index for an exposure such as "30". Raises ValueError when the model has no such exposure."""
    index = _tables().get_exposure_index_by_name(str(seconds), model.library_type, strict=True)
    if index is None:
        raise ValueError(f"the {model.name} has no {seconds} s exposure; choose one of {', '.join(exposures(model))}")
    return index


def exposures(model: Model) -> list[str]:
    """Exposures of a second or longer, in seconds, as the camera names them."""
    tables = _tables()
    table = {"5": tables.allowed_exposuresMini, "3": tables.allowed_exposuresD3}.get(
        model.library_type, tables.allowed_exposures
    )
    names = [v["name"] for v in table.values if "/" not in v["name"]]
    return [n for n in names if float(n) >= 1]


def filter_index(model: Model, name: str) -> int:
    if name not in model.filters:
        raise ValueError(f"the {model.name} has no {name!r} filter; choose one of {', '.join(model.filters)}")
    return model.filters[name]


def relay_name(scope_name: bytes) -> bytes:
    """The name the relay shows the app: the scope's own prefix with RELAY1, so the length stays the same."""
    return scope_name[:-6] + b"RELAY1"
