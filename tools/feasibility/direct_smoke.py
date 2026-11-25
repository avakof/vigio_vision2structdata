"""One real image, two constrained local generations; writes the raw result and measurements."""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import resource
import socket
import sys
import time
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / ".vt-state"
EVIDENCE = ROOT / "evidence/feasibility"
FIXTURE = ROOT / "fixtures/feasibility/invoice.png"
EXPECTED = {"reference": "VTX-4187", "currency": "EUR", "total": "37,25"}
SCHEMA = {
    "type": "object",
    "properties": {k: {"type": ["string", "null"]} for k in EXPECTED},
    "required": list(EXPECTED),
    "additionalProperties": False,
}
PROMPT = ("Read the invoice image. Return the printed invoice number as reference, "
          "the printed currency code as currency, and the printed total amount as total. "
          "Copy punctuation and decimal separators exactly. Use null if illegible. Output JSON.")


def make_fixture():
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new("RGB", (800, 420), "white")
    draw = ImageDraw.Draw(image)
    font = "/System/Library/Fonts/Supplemental/Arial.ttf"
    for x, y, size, line in [(48, 32, 46, "FACTURE / INVOICE"), (48, 118, 34, "Référence : VTX-4187"),
                              (48, 192, 32, "Atelier de la Rive"), (48, 308, 44, "TOTAL     37,25 EUR")]:
        draw.text((x, y), line, font=ImageFont.truetype(font, size), fill="black")
    draw.line((48, 276, 735, 276), fill="black", width=2)
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    image.save(FIXTURE)


def network_probe():
    """No document data sent. Test a numeric external address and a real loopback exchange."""
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2) as client, server.accept()[0] as peer:
            client.sendall(b"x")
            loopback_ok = peer.recv(1) == b"x"
    with socket.socket() as external:
        external.settimeout(3)
        result = external.connect_ex(("1.1.1.1", 443))
    if result not in (errno.EPERM, errno.EACCES) or not loopback_ok:
        raise RuntimeError(f"OS network isolation unverified: loopback={loopback_ok}, public errno={result}")
    return {"loopback": loopback_ok, "public_connect_errno": result}


def smoke(model_path: Path, probe: bool):
    from jsonschema import Draft202012Validator
    started = time.monotonic()
    import mlx.core as mx
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.structured import build_json_schema_logits_processor

    if not model_path.is_absolute() or not model_path.is_dir():
        raise ValueError("Use an absolute local model directory")
    data = {"status": "failed", "fixture": str(FIXTURE.relative_to(ROOT)),
            "fixture_sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            "reference_origin": "Deterministic synthetic print; expected values authored before inference.",
            "input_pixels": [800, 420], "artifact": "mlx-community/Qwen3.5-9B-MLX-4bit",
            "revision": "938d8919941c6e7efd3c7150eff7fe9d12afa631",
            "prompt": PROMPT, "schema": SCHEMA, "expected": EXPECTED,
            "schema_enforcement": "mlx_vlm.structured + llguidance",
            "settings": {"max_tokens": 96, "temperature": 0, "enable_thinking": False, "retries": 0},
            "python": sys.version.split()[0], "runtime": {p: metadata.version(p) for p in
                    ["mlx", "mlx-metal", "mlx-vlm", "llguidance", "transformers", "Pillow", "jsonschema"]},
            "network_probe": network_probe() if probe else None, "calls": []}
    model, processor = load(str(model_path), trust_remote_code=False)
    mx.synchronize()
    data["imports_and_model_load_s"] = time.monotonic() - started
    data["processors"] = {"model": type(model).__name__, "processor": type(processor).__name__,
                          "image_processor": type(processor.image_processor).__name__}
    prompt = apply_chat_template(processor, model.config, PROMPT, num_images=1, enable_thinking=False)
    validator = Draft202012Validator(SCHEMA)
    for tag in ["process_cold", "weights_resident"]:
        t0 = time.monotonic()
        grammar = build_json_schema_logits_processor(processor.tokenizer, SCHEMA)
        result = generate(model, processor, prompt=prompt, image=[str(FIXTURE)], temperature=0.0,
                          max_tokens=96, logits_processors=[grammar], verbose=False)
        mx.synchronize()
        item = {"mode": tag, "seconds": time.monotonic() - t0, "raw": result.text,
                "finish_reason": getattr(result, "finish_reason", None),
                "generated_tokens": result.generation_tokens, "prompt_tokens": result.prompt_tokens,
                "mlx_peak_bytes": mx.get_peak_memory(), "max_rss_bytes_macos": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
        try:
            observed = json.loads(result.text)
            item["schema_valid"] = validator.is_valid(observed)
            item["exact"] = observed == EXPECTED
        except ValueError:
            item.update(schema_valid=False, exact=False)
        data["calls"].append(item)
    data["status"] = ("passed" if all(c["exact"] and c["schema_valid"] and c["generated_tokens"] < 96 for c in data["calls"])
                      else "failed")
    data["total_process_work_s"] = time.monotonic() - started
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, default=STATE/"models/qwen")
    parser.add_argument("--make-fixture", action="store_true")
    parser.add_argument("--require-network-denial", action="store_true")
    args = parser.parse_args()
    if args.make_fixture:
        make_fixture()
        return 0
    os.environ.update(HF_HOME=str(STATE/"hf-offline"), HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                      HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1", TOKENIZERS_PARALLELISM="false")
    STATE.mkdir(mode=0o700, exist_ok=True)
    with (STATE/"inference.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = smoke(args.model_path.resolve(), args.require_network_denial)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE/"direct-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(result["status"], "sample count 1 image, 2 calls")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
