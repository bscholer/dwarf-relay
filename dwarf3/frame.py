"""Grab one JPEG from the scope's live RTSP stream with ffmpeg."""

import subprocess

STREAMS = {"tele": "ch0/stream0", "wide": "ch1/stream0"}
TIMEOUT_S = {"tele": 70, "wide": 25}  # the tele stream sends a keyframe about every 40 s


def grab(host: str, camera: str, width: int = 1280) -> bytes:
    if camera not in STREAMS:
        raise ValueError(f"camera must be tele or wide, not {camera}")
    cmd = [
        "ffmpeg", "-loglevel", "error", "-threads", "1", "-rtsp_transport", "tcp",
        "-i", f"rtsp://{host}:554/{STREAMS[camera]}",
        "-frames:v", "1", "-vf", f"scale={width}:-1", "-f", "image2", "-c:v", "mjpeg", "pipe:1",
    ]
    out = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S[camera])
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(f"no frame from the {camera} camera: {out.stderr.decode()[:200]}")
    return out.stdout
