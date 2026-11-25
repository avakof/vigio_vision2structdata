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
from .config import ProcessingError, QWEN_MODEL, QWEN_REVISION, Settings, digest
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


def _processor_check(state, python, versions):
    stamp_path = state / "models/qwen-processor-check.json"
    key = digest([QWEN_REVISION, versions, platform.platform(), (ASSETS / "check_processor.py").stat().st_mtime_ns])
    try:
        cached = json.loads(stamp_path.read_text())
        if cached.get("key") == key and cached.get("passed"):
            return cached
    except (OSError, ValueError):
        pass
    env = os.environ | {"HF_HOME": str(state / "runtime-offline/hf"), "HF_HUB_OFFLINE": "1",
                        "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                        "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false"}
    try:
        result = subprocess.run([str(python), "-I", str(ASSETS / "check_processor.py"), str(state / "models/qwen")],
                                env=env, capture_output=True, text=True, timeout=60)
        if result.returncode:
            return {"passed": False, "reason": result.stderr.strip()[-1000:]}
        check = {"key": key, "passed": True, **json.loads(result.stdout)}
        write_json(stamp_path, check)
        return check
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"passed": False, "reason": str(exc)}


def runtime_pins(filename: str) -> dict[str, str]:
    lines = (ASSETS / filename).read_text().splitlines()
    return dict(line.split("==") for line in lines if line and line[0].isalnum() and "==" in line)


def installed_versions(python: Path, pins: dict[str, str]) -> dict[str, str]:
    """Query the isolated interpreter and preserve missing packages for readiness diagnostics."""
    versions = {}
    if python.is_file():
        code = (
            "import json, re, sys, importlib.metadata as m; "
            "all_versions = {re.sub('[-_.]+', '-', p.metadata['Name']).lower():p.version for p in m.distributions()}; "
            "print(json.dumps({n:all_versions[n] for n in sys.argv[1:] if n in all_versions}))"
        )
        result = subprocess.run([str(python), "-I", "-c", code, *pins], capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            versions = json.loads(result.stdout)
    return versions


def runtime_info(settings: Settings) -> dict:
    state = settings.state_dir.resolve()
    python = state / "direct-env" / "bin" / "python"
    pins = runtime_pins("direct-requirements.txt")
    versions = installed_versions(python, pins)
    missing = _weights(state)
    conflicts = {p: {"installed": v, "required": pins[p]} for p, v in versions.items() if v != pins[p]}
    missing_packages = sorted(pins.keys() - versions.keys())
    host_ok = platform.system() == "Darwin" and platform.machine() == "arm64"
    processor = {"passed": False, "reason": "Required runtime or processor files missing."}
    if host_ok and not missing_packages and not conflicts and all(n.endswith(".safetensors") for n in missing):
        processor = _processor_check(state, python, versions)
    info = {"model": QWEN_MODEL, "revision": QWEN_REVISION, "versions": versions,
            "components": {"invalid_or_missing": missing, "mlx_runtime_found": bool(versions),
                           "version_conflicts": conflicts, "missing_runtime_packages": missing_packages,
                           "processor": processor},
            "ready": host_ok and processor["passed"] and not missing, "host_supported": host_ok}
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
    from .vision_heic import heic_info

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
            "runtime": runtime, "ready": runtime["ready"], "ingestion": {"heic": heic_info()},
            "readiness_basis": "Local component inventory and installed runtime versions. Real inference gates are recorded in evidence/acceptance.json.",
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
                smoke = ASSETS / "paddle-gate1.py"
                if not smoke.is_file():  # editable source checkout
                    smoke = ASSETS.parents[2] / "scripts/paddle-gate1.py"
                if not runtime_info(settings)["components"]["parser"]["ready"]:
                    with (paddle_dir / "smoke.log").open("w") as log:
                        result = subprocess.run(["/usr/bin/sandbox-exec", "-p",
                                                 "(version 1) (allow default) (deny network*)",
                                                 str(paddle_dir / "env/bin/python"), str(smoke)],
                                                env=env | {"VT_STATE_DIR": str(state),
                                                           "VT_INFERENCE_LOCK": str(settings.inference_lock)},
                                                stdout=log, stderr=subprocess.STDOUT)
                    if result.returncode:
                        raise ProcessingError("parser_smoke_failed", f"Full parser smoke failed; see {paddle_dir / 'smoke.log'}.")
        except ValueError as exc:
            raise ProcessingError("integrity_error", str(exc)) from exc
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ProcessingError("provision_failed", f"Provisioning interrupted: {exc}") from exc
        missing = _weights(state, verify=True)
        if missing:
            raise ProcessingError("integrity_error", "Failed verification for " + ", ".join(missing))
        write_json(model_dir.parent / "qwen-manifest.json", DIRECT_MANIFEST)
    return doctor(settings)
