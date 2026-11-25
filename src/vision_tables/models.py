"""Pinned component inventory and explicit provisioning. Discovery stays offline."""
from __future__ import annotations

import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .artifacts import write_json
from .config import ProcessingError, QWEN_MODEL, QWEN_REVISION, Settings

ASSETS = Path(__file__).with_name("assets")
DIRECT_MANIFEST = json.loads((ASSETS / "qwen.json").read_text())


def _matches(path: Path, entry: dict) -> bool:
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


def _weights(state: Path, *, verify: bool = False):
    directory = state / "models" / "qwen"
    stamp_file = state / "models" / "qwen-verified.json"
    try:
        cached = json.loads(stamp_file.read_text())
    except (OSError, ValueError):
        cached = {}
    stamps, missing = {}, []
    for entry in DIRECT_MANIFEST["files"]:
        path = directory / entry["name"]
        try:
            st = path.stat()
        except FileNotFoundError:
            missing.append(entry["name"])
            continue
        stamp = [st.st_dev, st.st_ino, st.st_ctime_ns, st.st_size, entry["digest"]]
        if path.is_symlink() or st.st_size != entry["size"]:
            missing.append(entry["name"])
        elif verify or cached.get(entry["name"]) != stamp:
            if _matches(path, entry):
                stamps[entry["name"]] = stamp
            else:
                missing.append(entry["name"])
        else:
            stamps[entry["name"]] = stamp
    if stamps != cached and stamps:
        write_json(stamp_file, stamps)
    return missing


def runtime_info(settings: Settings) -> dict:
    state = settings.state_dir.resolve()
    python = state / "direct-env" / "bin" / "python"
    versions = {}
    if python.is_file():
        code = (
            "import json, importlib.metadata as m; "
            "print(json.dumps({p:m.version(p) for p in "
            "('mlx-vlm','mlx','mlx-metal','transformers','pillow')}))"
        )
        result = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            versions = json.loads(result.stdout)
    missing = _weights(state)
    host_ok = platform.system() == "Darwin" and platform.machine() == "arm64"
    info = {"model": QWEN_MODEL, "revision": QWEN_REVISION, "versions": versions,
            "components": {"invalid_or_missing": missing, "mlx_runtime_found": bool(versions)},
            "ready": host_ok and bool(versions) and not missing, "host_supported": host_ok}
    if settings.pipeline == "hybrid":
        try:
            from .vision import hybrid_info
        except ImportError:
            parser = {"ready": False, "reason": "full local Paddle parser adapter unavailable"}
        else:
            parser = hybrid_info(settings)
        info["components"]["parser"] = parser
        info["ready"] = info["ready"] and parser["ready"]
    return info


def doctor(settings: Settings) -> dict:
    state = settings.state_dir.resolve()
    existing = next(p for p in (state, *state.parents) if p.exists())
    runtime = runtime_info(settings)
    hardware = {"machine": platform.machine(), "system": platform.platform(),
                "free_disk_bytes": shutil.disk_usage(existing).free}
    if platform.system() == "Darwin":
        for key, name in (("machdep.cpu.brand_string", "cpu"), ("hw.memsize", "memory_bytes")):
            result = subprocess.run(["sysctl", "-n", key], capture_output=True, text=True)
            if result.returncode == 0:
                hardware[name] = result.stdout.strip()
    return {"state_dir": str(state), "pipeline": settings.pipeline, "hardware": hardware,
            "python": sys.version.split()[0], "core_versions": {
                p: importlib.metadata.version(p) for p in ("pydantic", "pypdfium2", "Pillow", "jsonschema")},
            "runtime": runtime, "ready": runtime["ready"],
            "next": [] if runtime["ready"] else ["Run vision-tables models prepare and inspect component results."]}


def prepare(settings: Settings) -> dict:
    state = settings.state_dir.resolve()
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ProcessingError("unsupported_runtime", "The pinned MLX runtime requires an Apple Silicon Mac.")
    if settings.pipeline == "hybrid":
        raise ProcessingError("parser_setup_required", "See docs/HANDOFF.md for full parser provisioning and gate status.")
    uv = shutil.which("uv")
    if not uv:
        raise ProcessingError("missing_tool", "Install uv to provision the isolated MLX environment.")
    env = {**os.environ, "UV_CACHE_DIR": str(state / "uv-cache"), "HF_HOME": str(state / "hf-cache"),
           "HF_HUB_DISABLE_TELEMETRY": "1"}
    proc_env = state / "direct-env"
    model_dir = state / "models" / "qwen"
    model_dir.mkdir(parents=True, exist_ok=True)
    requirements = ASSETS / "direct-requirements.txt"
    with (state / "provision.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if not (proc_env / "bin/python").exists():
                subprocess.run([uv, "venv", "--python", sys.executable, str(proc_env)], check=True, env=env)
            subprocess.run([uv, "pip", "install", "--python", str(proc_env / "bin/python"),
                            "-r", str(requirements)], check=True, env=env)
            for entry in DIRECT_MANIFEST["files"]:
                path = model_dir / entry["name"]
                if _matches(path, entry):
                    continue
                temp = model_dir / (path.name + ".download")
                url = f"https://huggingface.co/{QWEN_MODEL}/resolve/{QWEN_REVISION}/{path.name}"
                subprocess.run(["curl", "--proto", "=https", "--proto-redir", "=https", "-fLsS",
                                "--retry", "3", "--connect-timeout", "30", "--max-time", "7200",
                                "-C", "-", "-o", str(temp), url], check=True)
                if not _matches(temp, entry):
                    temp.unlink(missing_ok=True)
                    raise ProcessingError("integrity_error", f"Digest or size mismatch for {path.name}")
                os.replace(temp, path)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ProcessingError("provision_failed", f"Provisioning interrupted: {exc}") from exc
        missing = _weights(state, verify=True)
        if missing:
            raise ProcessingError("integrity_error", "Failed verification for " + ", ".join(missing))
        write_json(model_dir.parent / "qwen-manifest.json", DIRECT_MANIFEST)
    return doctor(settings)
