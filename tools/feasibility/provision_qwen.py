"""Explicitly provision the pinned public, multimodal Qwen artifact (stdlib + curl)."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import subprocess
from pathlib import Path

MODEL_ID = "mlx-community/Qwen3.5-9B-MLX-4bit"
REVISION = "938d8919941c6e7efd3c7150eff7fe9d12afa631"


def download(destination: Path) -> dict:
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    base = f"https://huggingface.co/{MODEL_ID}"
    url = f"https://huggingface.co/api/models/{MODEL_ID}/tree/{REVISION}"
    curl = ["curl", "--proto", "=https", "--proto-redir", "=https", "-fLsS", "--retry", "4", "--connect-timeout", "30"]
    entries = json.loads(subprocess.check_output([*curl, url], timeout=120))
    names = {"README.md", "chat_template.jinja", "config.json", "model.safetensors.index.json",
             "preprocessor_config.json", "processor_config.json", "tokenizer.json",
             "tokenizer_config.json", "vocab.json", "video_preprocessor_config.json",
             "model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"}
    files = [row for row in entries if row.get("path") in names and row["type"] == "file"]
    if {row["path"] for row in files} != names:
        raise ValueError("Pinned artifact has an unexpected file inventory")

    def fetch(entry):
        path = destination / entry["path"]
        expected_size = entry["size"]

        def verify(candidate):
            if not candidate.is_file() or candidate.stat().st_size != expected_size:
                return None
            sha256 = hashlib.sha256()
            git_blob = hashlib.sha1(f"blob {expected_size}\0".encode())
            with candidate.open("rb") as reader:
                for chunk in iter(lambda: reader.read(8 << 20), b""):
                    sha256.update(chunk)
                    if "lfs" not in entry:
                        git_blob.update(chunk)
            digest = sha256.hexdigest()
            valid = (digest == entry["lfs"]["oid"] if "lfs" in entry
                     else git_blob.hexdigest() == entry["oid"])
            return digest if valid else None

        digest = verify(path)
        if not digest:
            part = path.with_suffix(path.suffix + ".part")
            subprocess.run([*curl, "--max-time", "7200", "-C", "-", "-o", str(part),
                            f"{base}/resolve/{REVISION}/{path.name}"], check=True)
            digest = verify(part)
            if not digest:
                raise ValueError(f"Integrity mismatch: {path.name}")
            os.replace(part, path)
        return {"path": path.name, "bytes": expected_size, "sha256": digest}

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        hashes = sorted(executor.map(fetch, files), key=lambda row: row["path"])
    return {"artifact": MODEL_ID, "revision": REVISION, "license": "Apache-2.0",
            "base_model": "Qwen/Qwen3.5-9B", "quantization": {"bits": 4, "group_size": 64},
            "files": hashes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    manifest = download(args.destination)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
