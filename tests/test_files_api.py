import pytest

from dwarf_relay.mcp_server import album_path


def test_album_path_stays_inside_the_album():
    assert album_path("DWARF_RAW_x/a.fits") == "/Astronomy/DWARF_RAW_x/a.fits"
    assert album_path("") == "/Astronomy"
    for bad in ("../etc", "a/../../b", "./a"):
        with pytest.raises(ValueError):
            album_path(bad)
