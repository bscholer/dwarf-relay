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
