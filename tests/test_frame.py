"""Frame grab: unknown camera is rejected before ffmpeg runs, a failed grab raises."""

import subprocess

import pytest

from dwarf_relay import frame


def test_unknown_camera_is_rejected():
    with pytest.raises(ValueError):
        frame.grab("1.2.3.4", "side")


def test_failed_grab_raises_with_the_ffmpeg_message(monkeypatch):
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"Connection refused")

    monkeypatch.setattr(frame.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Connection refused"):
        frame.grab("1.2.3.4", "wide")


def test_stream_path_matches_the_camera(monkeypatch):
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"jpeg", stderr=b"")

    monkeypatch.setattr(frame.subprocess, "run", fake_run)
    assert frame.grab("1.2.3.4", "tele") == b"jpeg"
    assert "rtsp://1.2.3.4:554/ch0/stream0" in seen[0]
    frame.grab("1.2.3.4", "wide")
    assert "rtsp://1.2.3.4:554/ch1/stream0" in seen[1]
