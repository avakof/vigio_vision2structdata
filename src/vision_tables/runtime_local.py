"""Offline subprocess boundary for the local model environments."""

import errno
import json
import os
import platform
import socket
import subprocess
import time
from functools import lru_cache
from pathlib import Path

from .config import ProcessingError
from .models import PADDLE_MANIFEST, installed_versions, runtime_info, runtime_pins
from .provision import matches
from .wire_json import parse_object

LAYOUT_REVISION = PADDLE_MANIFEST["layout"]["revision"]
VLM_REVISION = PADDLE_MANIFEST["vlm"]["revision"]
_PARSER_PINS = runtime_pins("paddle-requirements.txt")
_PROFILE = "(version 1) (allow default) (deny network*)"


@lru_cache(maxsize=64)
def _matches(path, ctime, inode, manifest_entry):
    return matches(path, dict(manifest_entry))


def hybrid_info(settings):
    home = settings.state_dir.resolve() / "paddle"
    info = {"ready": False, "model": "PaddleOCR-VL-1.6", "backend": "native",
            "device_requested": "cpu", "layout_revision": LAYOUT_REVISION,
            "recognition_revision": VLM_REVISION}
    try:
        files = [(home / "models" / component / entry["name"], entry)
                 for component, manifest in PADDLE_MANIFEST.items() for entry in manifest["files"]]
        missing = [f"{path.parent.name}/{path.name}" for path, entry in files
                   if path.is_symlink() or not path.is_file() or path.stat().st_size != entry["size"]]
        if missing:
            return info | {"reason": "Incomplete Paddle weights: " + ", ".join(missing)}
        report = parse_object((home / "output" / "report.json").read_bytes())
        if (report["pipeline_version"] != "v1.6" or report["paddle_device"] != "cpu"
                or report["layout"]["revision"] != LAYOUT_REVISION
                or report["recognition"]["revision"] != VLM_REVISION
                or not any(row["layout_boxes"] and row["recognized_blocks"] for row in report["passes"])):
            raise ValueError("Paddle smoke evidence does not match the pinned components")
        if platform.system() != "Darwin" or platform.machine() != "arm64":
            raise ValueError("The native Paddle adapter requires Apple Silicon macOS")
        python = home / "env" / "bin" / "python"
        versions = installed_versions(python, _PARSER_PINS)
        mismatches = {name: {"required": expected, "installed": versions.get(name)}
                      for name, expected in _PARSER_PINS.items() if versions.get(name) != expected}
        if mismatches:
            return info | {"reason": "The native parser environment differs from its pinned lock.",
                           "version_conflicts": mismatches}
        if any(report["packages"].get(p) != versions[p] for p in ("paddlepaddle", "paddleocr", "paddlex", "safetensors")):
            raise ValueError("Paddle runtime versions do not match the verified smoke")
        for path, entry in files:
            status = path.stat()
            if not _matches(path, status.st_ctime_ns, status.st_ino, tuple(entry.items())):
                raise ValueError(f"Paddle artifact checksum mismatch: {path.name}")
    except FileNotFoundError:
        return info | {"reason": "The isolated Paddle runtime or genuine full-parser smoke evidence is missing."}
    except ValueError as exc:
        return info | {"reason": str(exc)}
    except (KeyError, OSError, subprocess.SubprocessError, TypeError) as exc:
        return info | {"reason": f"Paddle components could not be verified ({type(exc).__name__})."}
    return info | {"ready": True, "versions": versions}


def _sandbox_prefix():
    if os.getenv("VT_VERIFY_OUTER_SANDBOX") == "1":
        try:
            with socket.socket() as probe:
                probe.settimeout(2)
                denied = probe.connect_ex(("1.1.1.1", 443)) in (errno.EPERM, errno.EACCES)
        except OSError as exc:
            denied = exc.errno in (errno.EPERM, errno.EACCES)
        if not denied:
            raise ProcessingError("offline_runtime_unavailable", "The inherited network restriction could not be proven.")
        return []
    return ["/usr/bin/sandbox-exec", "-p", _PROFILE]


def call(kind, request, directory, settings):
    """Persist subprocess logs and raw results under the job, including failures."""
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    host = settings.state_dir.resolve()
    env_dir = host / ("direct-env" if kind == "qwen" else "paddle/env")
    python = env_dir / "bin" / "python"
    model_dir = host / ("models/qwen" if kind == "qwen" else "paddle/models")
    if not python.is_file() or not model_dir.is_dir():
        raise ProcessingError("missing_model", f"The isolated {kind} environment or local model is missing.")
    if platform.system() != "Darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        raise ProcessingError("offline_runtime_unavailable", "This local model runtime requires macOS network isolation.")
    if kind == "qwen" and not runtime_info(settings.model_copy(update={"pipeline": "direct"}))["ready"]:
        raise ProcessingError("missing_model", "The local Qwen runtime or model artifacts did not match their pins.")
    cache = host / "runtime-offline"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = settings.inference_lock
    lock.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    env = os.environ | {
        "HF_HOME": str(cache / "hf"), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1", "TOKENIZERS_PARALLELISM": "false",
        "PADDLE_PDX_CACHE_HOME": str(cache / "paddlex"), "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
    }
    response = directory / "reply.json"
    command = [*_sandbox_prefix(), str(python), "-I", "-B", str(Path(__file__).with_name("runtime_worker.py")),
               kind, str(response)]
    payload = request | {"model_dir": str(model_dir), "lock": str(lock)}
    start = time.monotonic()
    try:
        with (directory / "runtime.log").open("xb") as logs:
            proc = subprocess.run(command, input=json.dumps(payload, ensure_ascii=False).encode(),
                                  stdout=logs, stderr=subprocess.STDOUT, env=env,
                                  timeout=900 if kind == "paddle" else 300)
        if proc.returncode:
            raise ProcessingError("local_runtime_failed", f"The {kind} runtime exited with code {proc.returncode}. See the local attempt log.")
        with response.open("rb") as result:
            reply = parse_object(result.read((8 << 20) + 1), limit=8 << 20)
    except subprocess.TimeoutExpired as exc:
        raise ProcessingError("local_runtime_timeout", f"The {kind} runtime or lock wait exceeded its deadline.") from exc
    except (OSError, ValueError) as exc:
        raise ProcessingError("local_runtime_failed", f"The {kind} runtime returned no readable local reply.") from exc
    return reply | {"seconds_including_lock_wait": time.monotonic() - start}
