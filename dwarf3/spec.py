"""Scope service spec v0.1: version and the operations each capability requires."""

SPEC_VERSION = "0.1"

CAPABILITY_OPERATIONS = {
    "status": ["getStatus"],
    "goto": ["gotoTarget"],
    "capture": ["startCapture", "stopCapture"],
    "darks": ["listDarks", "startDark"],
    "files": ["listSessions", "listFiles", "readFileChunk"],
}
BASE_OPERATIONS = ["getCapabilities"]
