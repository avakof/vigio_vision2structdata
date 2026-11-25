"""Pinned component inventory and explicit provisioning. Discovery stays offline."""
from __future__ import annotations

import fcntl
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
from .provision import download, matches

ASSETS = Path(__file__).with_name("assets")
DIRECT_MANIFEST = json.loads((ASSETS / "qwen.json").read_text())
PADDLE_MANIFEST = json.loads((ASSETS / "paddle.json").read_text())


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
            if matches(path, entry):
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
            "('mlx-vlm','mlx','mlx-metal','transformers','llguidance','pillow','jsonschema')}))"
        )
        result = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            versions = json.loads(result.stdout)
    missing = _weights(state)
    pins = dict(line.strip().split("==") for line in (ASSETS / "direct-requirements.txt").read_text().splitlines()
                if line and line[0].isalnum() and "==" in line)
    conflicts = {p: {"installed": v, "required": pins[p]} for p, v in versions.items() if v != pins[p]}
    host_ok = platform.system() == "Darwin" and platform.machine() == "arm64"
    info = {"model": QWEN_MODEL, "revision": QWEN_REVISION, "versions": versions,
            "components": {"invalid_or_missing": missing, "mlx_runtime_found": bool(versions), "version_conflicts": conflicts},
            "ready": host_ok and bool(versions) and not missing and not conflicts, "host_supported": host_ok}
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
    uv = shutil.which("uv")
    if not uv:
        raise ProcessingError("missing_tool", "Install uv to provision the isolated MLX environment.")
    env = {**os.environ, "UV_CACHE_DIR": str(state / "uv-cache"), "HF_HOME": str(state / "hf-cache"),
           "HF_HUB_DISABLE_TELEMETRY": "1"}
    model_dir = state / "models" / "qwen"
    model_dir.mkdir(parents=True, exist_ok=True)

    def install(proc_env, requirements):
        if not (proc_env / "bin/python").exists():
            subprocess.run([uv, "venv", "--python", sys.executable, str(proc_env)], check=True, env=env)
        subprocess.run([uv, "pip", "install", "--python", str(proc_env / "bin/python"),
                        "-r", str(ASSETS / requirements)], check=True, env=env)

    with (state / "provision.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            install(state / "direct-env", "direct-requirements.txt")
            download(model_dir, DIRECT_MANIFEST)
            if settings.pipeline == "hybrid":
                paddle_dir = state / "paddle"
                paddle_dir.mkdir(exist_ok=True)
                install(paddle_dir / "env", "paddle-requirements.txt")
                for component, manifest in PADDLE_MANIFEST.items():
                    download(paddle_dir / "models" / component, manifest)
                write_json(paddle_dir / "manifest.json", PADDLE_MANIFEST)
        except ValueError as exc:
            raise ProcessingError("integrity_error", str(exc)) from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ProcessingError("provision_failed", f"Provisioning interrupted: {exc}") from exc
        missing = _weights(state, verify=True)
        if missing:
            raise ProcessingError("integrity_error", "Failed verification for " + ", ".join(missing))
        write_json(model_dir.parent / "qwen-manifest.json", DIRECT_MANIFEST)
    return doctor(settings)
