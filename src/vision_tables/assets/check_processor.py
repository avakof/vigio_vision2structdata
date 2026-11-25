"""Probe the installed MLX processor with synthetic pixels. Runs in the separate MLX environment."""
import json
import sys

import mlx.core as mx
from mlx_vlm.structured import build_json_schema_logits_processor
from mlx_vlm.utils import load_processor
from PIL import Image


def main():
    processor = load_processor(sys.argv[1], add_detokenizer=False, local_files_only=True, trust_remote_code=False)
    encoded = processor(text=["<|vision_start|><|image_pad|><|vision_end|>"],
                        images=[Image.new("RGB", (56, 56), "white")], return_tensors="np")
    grammar = build_json_schema_logits_processor(processor.tokenizer, {
        "type": "object", "properties": {"value": {"type": "string"}},
        "required": ["value"], "additionalProperties": False,
    })
    print(json.dumps({"processor": type(processor).__name__, "pixel_shape": list(encoded["pixel_values"].shape),
                      "schema_decoder": type(grammar).__name__, "device": mx.device_info()["device_name"]}))


if __name__ == "__main__":
    main()
