"""FTP access to the scope album."""
import ftplib
import re
from pathlib import Path

RAW_SESSION = re.compile(r"^DWARF_RAW_")
NON_FRAME = re.compile(r"^(stacked|dark_)")


def count_frames(names: list[str]) -> int:
    """Count raw frames in a session folder. Rejected frames also count as shots."""
    return sum(1 for n in names if n.endswith(".fits") and not NON_FRAME.match(n))


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

    def _entries(self, path: str):
        lines: list[str] = []
        self.ftp.dir(path, lines.append)
        for line in lines:
            parts = line.split(None, 8)
            if len(parts) == 9 and parts[8] not in (".", ".."):
                yield parts[8], {"dir": line.startswith("d"), "size": int(parts[4])}

    def close(self) -> None:
        self.ftp.quit()
