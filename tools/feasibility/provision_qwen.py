"""Provision and verify the same pinned Qwen inventory used by the application CLI."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# The feasibility runner also works with a bare Python 3.12 before the core package is installed.
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from vision_tables.provision import download


def provision(destination: Path) -> dict:
    inventory = json.loads((ROOT / "src/vision_tables/assets/qwen.json").read_text())
    download(destination, inventory)
    files = []
    for entry in inventory["files"]:
        path = destination / entry["name"]
        with path.open("rb") as reader:
            digest = hashlib.file_digest(reader, "sha256").hexdigest()
        files.append({"path": entry["name"], "bytes": entry["size"], "sha256": digest})
    return {"artifact": inventory["artifact"], "revision": inventory["revision"],
            "license": inventory["license"], "base_model": inventory["base"],
            "quantization": inventory["quantization"], "files": files}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    manifest = provision(args.destination)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
