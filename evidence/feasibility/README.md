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

## Measured on the M3 Max

Both generations passed on 2026-11-25. Each returned the exact printed reference `VTX-4187`, currency `EUR` and total `37,25`. Each consumed 383 prompt tokens, emitted 29 tokens and ended at a stop token. The process verified a successful loopback exchange and an OS denial on an external socket.

| Measurement | Observed |
| --- | ---: |
| Imports and weight loading | 5.01 s |
| First constrained generation | 4.86 s |
| Second generation in the same process | 1.10 s |
| End-to-end wall time, both calls | 12.55 s |
| Highest MLX allocation | 6.46 GiB |
| Maximum process RSS | 6.67 GiB |
| macOS peak memory footprint | 8.29 GiB |

Sample size was one synthetic printed page, read twice. The restricted file-to-tables CLI run and real-document evaluation are recorded separately in the release acceptance report. [Outputs and generation measurements](direct-smoke.json), [process measurements](direct-smoke-process.json), [verified artifact inventory](qwen-artifact.json), [release acceptance](../acceptance.json).
