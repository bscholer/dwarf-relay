"""FTP access to the scope album."""
import ftplib
import json
import re
import urllib.request
from pathlib import Path

RAW_SESSION = re.compile(r"^DWARF_RAW_")
NON_FRAME = re.compile(r"^(stacked|dark_)")


FRAME_EXTENSIONS = (".fits", ".tif", ".tiff")


def count_frames(names: list[str]) -> int:
    """Count raw frames in a session folder. Rejected frames also count as shots."""
    return sum(1 for n in names if n.lower().endswith(FRAME_EXTENSIONS) and not NON_FRAME.match(n))


def count_failed(names: list[str]) -> int:
    """Count frames the scope tagged failed. They stay in the session folder."""
    return sum(1 for n in names if n.startswith("failed_") and n.endswith(".fits"))


SESSION_NAME = re.compile(r"^DWARF_RAW_[A-Za-z0-9 ._-]+$")
API_PORT = 8082
MEDIA_TYPE_ASTRO, SUB_TYPE_SESSION = 4, 1


def delete_session(host: str, session: str, urlopen=urllib.request.urlopen) -> bool:
    """Delete one capture session through the scope album API, as the DWARFLAB app does.

    The app names a session by its stacked.jpg entry. Return False when the scope has no such session."""
    if not SESSION_NAME.match(session) or ".." in session:
        raise ValueError("session must be a DWARF_RAW_ folder name")
    entry = {
        "fileName": session,
        "filePath": f"/DWARF3/Astronomy/{session}/stacked.jpg",
        "mediaType": MEDIA_TYPE_ASTRO,
        "subType": SUB_TYPE_SESSION,
    }
    request = urllib.request.Request(
        f"http://{host}:{API_PORT}/album/delete",
        data=json.dumps({"datas": [entry]}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as resp:
        reply = json.loads(resp.read())
    results = reply.get("data") or []
    return reply.get("code") == 0 and bool(results) and bool(results[0].get("isSuccess"))


def sd_free_gb(host: str, urlopen=urllib.request.urlopen) -> float | None:
    """Read the free space of the scope SD card from deviceInfo. Return None when the scope does not say."""
    request = urllib.request.Request(
        f"http://{host}:{API_PORT}/deviceInfo",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as resp:
            card = (json.loads(resp.read()).get("data") or {}).get("sdCardInfo") or {}
    except (OSError, ValueError):
        return None
    free = card.get("availableSize")
    return float(free) if card.get("hasSdcard") and isinstance(free, (int, float)) else None


def raw_sessions(names: list[str]) -> set[str]:
    return {n for n in names if RAW_SESSION.match(n)}


class Album:
    def __init__(self, host: str):
        self.ftp = ftplib.FTP(host, timeout=30)
        self.ftp.login()

    def names(self, path: str) -> list[str]:
        return [n.rsplit("/", 1)[-1] for n in self.ftp.nlst(path)]

    def mirror(self, path: str, dest: Path, log=print) -> int:
        """Copy a folder tree to dest. Skip files with a matching local size."""
        copied = 0
        dest.mkdir(parents=True, exist_ok=True)
        for name, facts in self._entries(path):
            remote, local = f"{path}/{name}", dest / name
            if facts["dir"]:
                copied += self.mirror(remote, local, log)
            elif not local.exists() or local.stat().st_size != facts["size"]:
                partial = local.with_name(local.name + ".part")
                with partial.open("wb") as out:
                    self.ftp.retrbinary(f"RETR {remote}", out.write)
                partial.replace(local)
                log(f"copied {remote}")
                copied += 1
        return copied

    def entries(self, path: str) -> list[dict]:
        return [{"name": n, "size": f["size"], "dir": f["dir"]} for n, f in self._entries(path)]

    def read(self, path: str, offset: int, length: int) -> bytes:
        """Read length bytes from offset. Return fewer bytes at the end of the file."""
        data = bytearray()
        self.ftp.voidcmd("TYPE I")
        conn = self.ftp.transfercmd(f"RETR {path}", rest=offset)
        try:
            while len(data) < length:
                block = conn.recv(min(65536, length - len(data)))
                if not block:
                    break
                data.extend(block)
        finally:
            conn.close()
            try:
                self.ftp.voidresp()
            except ftplib.Error:
                pass
        return bytes(data)

    def _entries(self, path: str):
        lines: list[str] = []
        self.ftp.dir(path, lines.append)
        for line in lines:
            parts = line.split(None, 8)
            if len(parts) == 9 and parts[8] not in (".", ".."):
                yield parts[8], {"dir": line.startswith("d"), "size": int(parts[4])}

    def close(self) -> None:
        self.ftp.quit()
