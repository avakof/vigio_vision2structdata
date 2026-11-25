"""Explicit resumable downloads of public files from the pinned model inventory."""
from __future__ import annotations

import concurrent.futures
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path


def matches(path: Path, entry: dict) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != entry["size"]:
        return False
    if entry["algorithm"] == "sha256":
        hashed = hashlib.sha256()
    else:
        hashed = hashlib.sha1(f"blob {entry['size']}\0".encode())
    with path.open("rb") as fp:
        while block := fp.read(8 << 20):
            hashed.update(block)
    return hashed.hexdigest() == entry["digest"]


def download(directory: Path, manifest: dict, workers: int = 8):
    """Only the packaged, pinned manifests are passed here; no document input."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    base = "https://huggingface.co/{artifact}/resolve/{revision}/".format(**manifest)
    curl = ["curl", "--proto", "=https", "--proto-redir", "=https", "-fLsS",
            "--connect-timeout", "30", "--retry", "4", "--retry-all-errors", "--max-time", "600"]
    for entry in sorted(manifest["files"], key=lambda f: f["size"]):
        path = directory / entry["name"]
        if matches(path, entry):
            continue
        size = entry["size"]
        # A partial connection can be resumed at chunk granularity. Validate the
        # Content-Range as well as length, then hash the complete assembled file.
        chunk = 4 << 20
        parts = directory / ".ranges" / entry["digest"]
        parts.mkdir(parents=True, exist_ok=True)

        def get(start):
            end = min(start + chunk, size) - 1
            fragment = parts / str(start)
            if fragment.is_file() and fragment.stat().st_size == end - start + 1:
                return fragment
            with tempfile.NamedTemporaryFile(dir=parts, delete=False) as temp:
                temporary = Path(temp.name)
            try:
                args = ["-r", f"{start}-{end}"] if size > chunk else []
                result = subprocess.run([*curl, *args, "-w", "%{http_code} %header{content-range}",
                                         "-o", str(temporary), base + path.name],
                                        capture_output=True, text=True, check=True)
                header = result.stdout.strip()
                if size > chunk and header != f"206 bytes {start}-{end}/{size}":
                    raise ValueError(f"{path.name}: unexpected HTTP range {header!r}")
                if temporary.stat().st_size != end - start + 1:
                    raise ValueError(f"{path.name}: partial chunk at {start}")
                os.replace(temporary, fragment)
            finally:
                temporary.unlink(missing_ok=True)
            return fragment

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            fragments = list(pool.map(get, range(0, size, chunk)))
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
            assembled = Path(output.name)
            for fragment in fragments:
                output.write(fragment.read_bytes())
        try:
            if not matches(assembled, entry):
                for fragment in fragments:
                    fragment.unlink(missing_ok=True)
                raise ValueError(f"{path.name}: upstream manifest digest mismatch")
            os.replace(assembled, path)
        finally:
            assembled.unlink(missing_ok=True)
        for fragment in fragments:
            fragment.unlink(missing_ok=True)
