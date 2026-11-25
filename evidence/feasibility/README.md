# Direct feasibility run

The fixture is a single 800 × 420 bilingual synthetic invoice. Its reference, currency and printed decimal-comma total were set before inference. The prompt asks for the three fields without supplying their values. Each result must independently pass JSON Schema validation and an exact-value comparison. This probe measures model/runtime compatibility on a small printed image. Business accuracy is evaluated separately.

The pinned artifact is `mlx-community/Qwen3.5-9B-MLX-4bit`, revision `938d8919941c6e7efd3c7150eff7fe9d12afa631`. Its model card identifies the instructed Qwen3.5-9B and the 4-bit conversion. The project inventory pins the tokenizer, processor, chat template and both shards. [Quantization card](https://huggingface.co/mlx-community/Qwen3.5-9B-MLX-4bit/blob/938d8919941c6e7efd3c7150eff7fe9d12afa631/README.md), [upstream Qwen model](https://huggingface.co/Qwen/Qwen3.5-9B).

MLX-VLM 0.8.1 constructs the schema decoder with `build_json_schema_logits_processor`. The still-image processor initialization also constructs a video processor and requires Torch and Torchvision. The initial runtime failed that check. The final direct lock includes the tested versions. The runtime also installs upstream MLX-VLM's declared `mlx-audio` and `miniaudio` dependencies. The application's model input consists of page images, local layout evidence and extraction prompts. [Processor probe](processor.json), [pinned MLX-VLM source](https://github.com/Neywa-Labs/mlx-vlm/tree/v0.8.1).

Run from the repository root after installing the core CLI and preparing the direct runtime in `.vt-state`.

```sh
.venv/bin/vision-tables models prepare --pipeline direct --state-dir "$PWD/.vt-state"
python3 tools/feasibility/provision_qwen.py "$PWD/.vt-state/models/qwen" --manifest evidence/feasibility/qwen-artifact.json
/usr/bin/time -l /usr/bin/sandbox-exec -f tools/feasibility/offline.sb \
  .vt-state/direct-env/bin/python tools/feasibility/direct_smoke.py --require-network-denial
```

The checked-in image can be regenerated with `.vt-state/direct-env/bin/python tools/feasibility/direct_smoke.py --make-fixture` using the macOS Arial font. A common POSIX lock serializes both generations with other project inference. The subprocess verifies an external TCP connection is denied by the OS and completes a loopback exchange. The first call starts in a fresh process after loading local weights. The second keeps the model resident. OS disk-cache state is uncontrolled. Output includes raw strings, input and generated token counts, enforced schema, exact matches, elapsed times, MLX peak allocation and the process RSS high-water mark.

The post-run `direct-smoke.json` contains the result for each call. The application and route comparison use the separate release acceptance report.
