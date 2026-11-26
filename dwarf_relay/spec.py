"""Scope service spec v0.2: version and the operations each capability requires."""

SPEC_VERSION = "0.2"

CAPABILITY_OPERATIONS = {
    "status": ["getStatus"],
    "setup": ["prepareScope", "calibrateScope"],
    "goto": ["gotoTarget"],
    "capture": ["startCapture", "stopCapture"],
    "darks": ["listDarks", "startDark"],
    "files": ["listSessions", "listFiles", "readFileChunk"],
    "delete": ["deleteSession"],
    "preview": ["getFrame"],
    "pointing": ["pointScope"],
    "events": [],  # GET /api/events, a server-sent stream of status changes
}
BASE_OPERATIONS = ["getCapabilities"]
