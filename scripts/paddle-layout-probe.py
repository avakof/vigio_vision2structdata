"""Exercise the native layout stage with the installed v1.6 parser configuration."""

import argparse
import fcntl
import hashlib
import json
import os
import platform
import resource
import runpy
import time
from importlib import metadata
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model_dir", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    model_dir = arguments.model_dir.resolve()
    gate = runpy.run_path(str(Path(__file__).with_name("paddle-gate1.py")))
    image = output / "invoice.png"
    gate["create_invoice"](image)
    with (model_dir / "inference.pdiparams").open("rb") as weights:
        digest = hashlib.file_digest(weights, "sha256").hexdigest()
    if digest != gate["WEIGHTS"]["layout/inference.pdiparams"][1]:
        raise ValueError("PP-DocLayoutV3 weights did not match the Gate 1 pin")
    os.environ.update(HF_HOME=str(output / "hf"), HF_HUB_OFFLINE="1",
                      PADDLE_PDX_CACHE_HOME=str(output / "paddlex"), PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK="True")
    started = time.perf_counter()
    lock_path = gate["inference_lock"]()
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        acquired = time.perf_counter()
        import paddle
        from paddleocr import LayoutDetection
        from paddlex.inference.pipelines import load_pipeline_config
        from PIL import Image, ImageDraw

        paddle.set_device("cpu")
        options = load_pipeline_config("PaddleOCR-VL-1.6")["SubModules"]["LayoutDetection"]
        options = {name: options[name] for name in ("model_name", "threshold", "layout_nms",
                                                    "layout_unclip_ratio", "layout_merge_bboxes_mode")}
        initialized = time.perf_counter()
        detector = LayoutDetection(**options, model_dir=str(model_dir), device="cpu", enable_mkldnn=False)
        init_seconds = time.perf_counter() - initialized
        predictor = detector.paddlex_predictor
        with Image.open(image) as fixture:
            width, height = fixture.size
        report = {
            "platform": platform.platform(), "machine": platform.machine(), "python": platform.python_version(),
            "packages": {name: metadata.version(name) for name in (
                "paddlepaddle", "paddleocr", "paddlex", "safetensors", "numpy", "opencv-contrib-python", "Pillow"
            )},
            "lock": str(lock_path), "lock_wait_seconds": acquired - started,
            "import_seconds": initialized - acquired, "initialization_seconds": init_seconds,
            "paddle_device": paddle.device.get_device(), "paddle_compiled_with_cuda": paddle.is_compiled_with_cuda(),
            "layout": gate["component"](predictor), "layout_revision": gate["LAYOUT_REVISION"],
            "layout_weight_sha256": digest, "fixture_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
            "image_size_px": [width, height], "coordinate_space": "source image pixels, top-left origin",
            "configuration": options, "passes": [],
        }
        try:
            for label in ("cold", "warm"):
                before = time.perf_counter()
                result, = detector.predict(str(image), batch_size=1, layout_shape_mode="auto", filter_overlap_boxes=False)
                elapsed = time.perf_counter() - before
                save = output / label
                save.mkdir()
                result.save_to_json(save_path=str(save))
                boxes = result.json["res"]["boxes"]
                if not boxes or any(not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height)
                                    for x0, y0, x1, y1 in (box["coordinate"] for box in boxes)):
                    raise ValueError("The detector returned absent or invalid source-pixel layout boxes")
                with Image.open(image) as fixture:
                    overlay = fixture.convert("RGB")
                draw = ImageDraw.Draw(overlay)
                for index, region in enumerate(boxes, 1):
                    bounds = region["coordinate"]
                    draw.rectangle(bounds, outline="#b42319", width=2)
                    draw.text((bounds[0], max(0, bounds[1] - 13)), f"{index}. {region['label']}", fill="#b42319")
                overlay.save(save / "layout.png")
                report["passes"].append({"label": label, "seconds": elapsed,
                                         "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                                         "boxes": boxes})
        finally:
            detector.close()
    (output / "layout-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
