"""Isolated entry point for native model environments, with no core dependencies."""

import fcntl
import json
import os
import resource
import sys
import time
from pathlib import Path


def qwen(request):
    import mlx.core as mx
    from mlx_vlm import generate, load
    from mlx_vlm.prompt_utils import apply_chat_template
    from mlx_vlm.structured import build_json_schema_logits_processor

    start = time.monotonic()
    model, processor = load(request["model_dir"], trust_remote_code=False)
    image = request["image"]
    prompt = apply_chat_template(processor, model.config, request["prompt"],
                                 num_images=int(image is not None), enable_thinking=False)
    grammar = build_json_schema_logits_processor(processor.tokenizer, request["schema"])
    mx.synchronize()
    loaded = time.monotonic()
    result = generate(model, processor, prompt=prompt, image=[image] if image else None,
                      temperature=0.0, max_tokens=request["max_tokens"],
                      logits_processors=[grammar], verbose=False)
    mx.synchronize()
    return {
        "raw": result.text,
        "finish_reason": getattr(result, "finish_reason", None),
        "generated_tokens": result.generation_tokens,
        "prompt_tokens": result.prompt_tokens,
        "load_seconds": loaded - start,
        "generation_seconds": time.monotonic() - loaded,
        "mlx_peak_bytes": mx.get_peak_memory(),
    }


class BoundedLayout:
    """Limit region recognition work after the official layout pass."""

    def __init__(self, predictor, limit):
        self.predictor = predictor
        self.limit = limit
        self.omitted = 0

    def __getattr__(self, name):
        return getattr(self.predictor, name)

    def __call__(self, *args, **kwargs):
        for result in self.predictor(*args, **kwargs):
            self.omitted += max(0, len(result["boxes"]) - self.limit)
            result["boxes"] = result["boxes"][:self.limit]
            yield result


def paddle_parse(request):
    import paddle
    from paddleocr import PaddleOCRVL

    base = Path(request["model_dir"])
    paddle.set_device("cpu")
    pipeline = PaddleOCRVL(
        pipeline_version="v1.6", device="cpu", vl_rec_backend="native", use_queues=False,
        use_layout_detection=True, use_doc_orientation_classify=False, use_doc_unwarping=False,
        enable_mkldnn=False, layout_detection_model_dir=str(base / "layout"),
        vl_rec_model_dir=str(base / "vlm"),
    )
    inner = pipeline.paddlex_pipeline._pipeline
    layout = BoundedLayout(inner.layout_det_model, request["max_regions"])
    inner.layout_det_model = layout
    (page,) = pipeline.predict(request["image"], max_new_tokens=request["max_tokens"])
    serial = page.json["res"]
    return {
        "layout": serial["layout_det_res"], "blocks": serial["parsing_res_list"],
        "width": serial["width"], "height": serial["height"],
        "omitted_regions": layout.omitted, "device": paddle.device.get_device(),
        "components": {
            name: {"class": type(predictor).__name__, "device": getattr(predictor, "device", None)}
            for name, predictor in (("layout", layout.predictor), ("recognition", inner.vl_rec_model))
        },
    }


if __name__ == "__main__":
    transport = sys.argv[1]
    request = json.load(sys.stdin)
    handler = {"qwen": qwen, "paddle": paddle_parse}[transport]
    with open(request["lock"], "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        start = time.monotonic()
        payload = handler(request)
        payload["runtime_seconds"] = time.monotonic() - start
        payload["peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    output = Path(sys.argv[2])
    staging = output.with_suffix(".tmp")
    staging.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    os.replace(staging, output)
