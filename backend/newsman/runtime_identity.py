"""A cross-Windows/WSL workspace fingerprint; no configuration values are exposed."""

import hashlib
from pathlib import Path
import re
import uuid

INSTANCE_ID = str(uuid.uuid4())


def workspace_id(path=None):
    root = Path(path).resolve() if path else Path(__file__).resolve().parents[2]
    normalized = root.as_posix().lower()
    normalized = re.sub(r"^/mnt/([a-z])/", r"\1:/", normalized)
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


if __name__ == "__main__":
    import sys

    print(workspace_id(sys.argv[1] if len(sys.argv) > 1 else None))
