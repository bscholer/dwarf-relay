"""Control a DWARF 3 smart telescope."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("dwarf-relay")
except PackageNotFoundError:  # running from a checkout that is not installed
    __version__ = "unknown"
