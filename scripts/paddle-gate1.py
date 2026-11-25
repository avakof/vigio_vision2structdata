"""Run the native PaddleOCR-VL 1.6 pipeline against one synthetic invoice."""

import fcntl
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time


WORKTREE = Path(__file__).resolve().parents[1]
STATE = Path(os.environ.get("VT_STATE_DIR", WORKTREE / ".vt-state")).resolve() / "paddle"
LOCK = Path(os.environ.get("VT_INFERENCE_LOCK", STATE.parent / "inference.lock"))
LAYOUT_REVISION = "241f8bdfc77a7c7bee915a5057aaee58c235a8d3"
VLM_REVISION = "132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd"
WEIGHTS = {
    "layout/inference.pdiparams": (
        130806572,
        "70bd316b0582769ec968829fd1feb1a6a58b7c941b938327e551b6b12b45c137",
    ),
    "vlm/model.safetensors": (
        1917255968,
        "85a479d506a11e724e7285d395c551be69f41dbc16b6342d3cacfb189aed71db",
    ),
}


def create_invoice(path):
    subprocess.run(
        [
            "/usr/bin/sips", "-s", "format", "png",
            str(Path(__file__).with_name("paddle-gate1-invoice.svg")),
            "--out", str(path),
        ],
        check=True,
    )


def verify_weights():
    problems = []
    for name, (size, digest) in WEIGHTS.items():
        path = STATE / "models" / name
        try:
            actual_size = path.stat().st_size
            if actual_size != size:
                problems.append(f"{name}: have {actual_size} of {size} bytes")
                continue
            with path.open("rb") as weights:
                if hashlib.file_digest(weights, "sha256").hexdigest() != digest:
                    problems.append(f"{name}: SHA-256 mismatch")
        except FileNotFoundError:
            problems.append(f"{name}: missing, expected {size} bytes")
    if problems:
        raise SystemExit("Model provisioning incomplete\n" + "\n".join(problems))


def component(predictor):
    return {
        "class": f"{type(predictor).__module__}.{type(predictor).__name__}",
        "device": getattr(predictor, "device", None),
        "model_name": predictor.model_name,
        "dtype": getattr(predictor, "dtype", None),
    }


def run():
    import paddle
    from paddleocr import PaddleOCRVL

    output = STATE / "output"
    output.mkdir(exist_ok=True)
    image_path = output / "invoice.png"
    paddle.set_device("cpu")
    started = time.perf_counter()
    pipeline = PaddleOCRVL(
        pipeline_version="v1.6",
        device="cpu",
        vl_rec_backend="native",
        layout_detection_model_dir=str(STATE / "models" / "layout"),
        vl_rec_model_dir=str(STATE / "models" / "vlm"),
        use_layout_detection=True,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_queues=False,
        enable_mkldnn=False,
    )
    outer = pipeline.paddlex_pipeline
    inner = getattr(outer, "_pipeline", outer)
    report = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("paddlepaddle", "paddleocr", "paddlex", "safetensors")
        },
        "pipeline_version": pipeline.pipeline_version,
        "paddle_device": paddle.device.get_device(),
        "paddle_compiled_with_cuda": paddle.is_compiled_with_cuda(),
        "input_image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
        "page_pixels": [700, 480],
        "layout": component(inner.layout_det_model) | {"revision": LAYOUT_REVISION},
        "recognition": component(inner.vl_rec_model) | {"revision": VLM_REVISION},
        "init_seconds": time.perf_counter() - started,
        "passes": [],
    }
    print(json.dumps(report, ensure_ascii=False), flush=True)
    for label in ("cold", "warm"):
        pass_dir = output / label
        pass_dir.mkdir(exist_ok=True)
        started = time.perf_counter()
        results = list(pipeline.predict(str(image_path), max_new_tokens=768))
        elapsed = time.perf_counter() - started
        if len(results) != 1:
            raise RuntimeError(f"Expected one page, got {len(results)}")
        result = results[0]
        result.save_to_json(save_path=str(pass_dir))
        result.save_to_markdown(save_path=str(pass_dir))
        boxes = result["layout_det_res"]["boxes"]
        blocks = result["parsing_res_list"]
        if not boxes or not any(str(block.content).strip() for block in blocks):
            raise RuntimeError("No detected boxes or recognized block text")
        report["passes"].append({
            "label": label,
            "prediction_seconds": elapsed,
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "layout_boxes": len(boxes),
            "recognized_blocks": len(blocks),
        })
        print(json.dumps(report["passes"][-1]), flush=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--fixture-only"]):
        raise SystemExit("usage: paddle-gate1.py [--fixture-only]")
    output = STATE / "output"
    output.mkdir(parents=True, exist_ok=True)
    create_invoice(output / "invoice.png")
    if sys.argv[1:]:
        raise SystemExit(0)
    verify_weights()
    os.environ.update({
        "HF_HOME": str(STATE / "hf"),
        "HF_HUB_OFFLINE": "1",
        "PADDLE_PDX_CACHE_HOME": str(STATE / "paddlex"),
        "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK": "True",
    })
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        run()
