"""Verify all files in an extracted handoff archive from its root directory."""
import hashlib
import json
from pathlib import Path


checksums = json.loads(Path("handoff-sha256.json").read_text())
for filename, expected in checksums.items():
    path = Path(filename)
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    if h.hexdigest() != expected:
        raise ValueError(f"Checksum mismatch: {filename}")
print(f"Verified {len(checksums)} files")
