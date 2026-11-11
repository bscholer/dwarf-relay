import json

from dwarf3.files import count_frames, raw_sessions


def test_count_includes_rejected_frames_and_excludes_stack_outputs():
    names = [
        "15s60_Duo-Band_20261105-225808862_36C.fits",
        "failed_15s60_Duo-Band_20261105-225753853_36C.fits",
        "stacked-16_15s60_Duo-Band_20261105-225739638.fits",
        "stacked.jpg",
        "shotsInfo.json",
        "Thumbnail",
    ]
    assert count_frames(names) == 2


def test_raw_sessions_ignore_other_album_folders():
    assert raw_sessions(["CALI_FRAME", "DWARF_DARK", "DWARF_RAW_TELE_X", "RESTACKED"]) == {"DWARF_RAW_TELE_X"}


def test_count_frames_accepts_tiff_frames_and_skips_stacks_and_images():
    from dwarf3.files import count_frames

    names = ["a_22C.tif", "b.fits", "failed_c.fits", "stacked-16_x.fits", "dark_1.fits", "stacked.jpg", "img_reference.png", "shotsInfo.json"]
    assert count_frames(names) == 3


def _fake_urlopen(reply, seen):
    import io

    class Response(io.BytesIO):
        def __enter__(self):
            return self

    def urlopen(request, timeout=None):
        seen.append(request)
        return Response(json.dumps(reply).encode())

    return urlopen


def test_delete_session_sends_the_entry_the_app_sends_and_reads_the_result():
    from dwarf3.files import delete_session

    seen = []
    ok = {"code": 0, "data": [{"isSuccess": True}]}
    assert delete_session("scope", "DWARF_RAW_TELE_x_2026-11-11-01-03-07-760", _fake_urlopen(ok, seen))
    request = seen[0]
    assert request.full_url == "http://scope:8082/album/delete"
    assert json.loads(request.data) == {
        "datas": [
            {
                "fileName": "DWARF_RAW_TELE_x_2026-11-11-01-03-07-760",
                "filePath": "/DWARF3/Astronomy/DWARF_RAW_TELE_x_2026-11-11-01-03-07-760/stacked.jpg",
                "mediaType": 4,
                "subType": 1,
            }
        ]
    }
    missing = {"code": 0, "data": [{"isSuccess": False}]}
    assert not delete_session("scope", "DWARF_RAW_nope", _fake_urlopen(missing, []))


def test_delete_session_refuses_names_that_could_reach_outside_a_raw_session():
    import pytest

    from dwarf3.files import delete_session

    for bad in ("../Normal_Photos", "DWARF_RAW_x/../../y", "Videos", "DWARF_RAW_a/b", ""):
        with pytest.raises(ValueError):
            delete_session("scope", bad, lambda *_a, **_k: pytest.fail("called the scope"))


def test_sd_free_gb_reads_device_info_and_gives_none_when_the_scope_is_silent():
    from dwarf3.files import sd_free_gb

    card = {"code": 0, "data": {"sdCardInfo": {"availableSize": 89, "hasSdcard": True, "totalSize": 104}}}
    assert sd_free_gb("scope", _fake_urlopen(card, [])) == 89.0
    nocard = {"code": 0, "data": {"sdCardInfo": {"availableSize": 0, "hasSdcard": False}}}
    assert sd_free_gb("scope", _fake_urlopen(nocard, [])) is None

    def down(*_a, **_k):
        raise OSError("unreachable")

    assert sd_free_gb("scope", down) is None
