# Gate 1 · PaddleOCR-VL-1.6 on Apple Silicon

**2026-11-25 · blocked at provisioning.** The native full-parser command stopped at the incomplete-weight check before importing PaddlePaddle. No real layout, recognition, device placement, boxes or inference latency were observed. The provisioned parser’s offline inference remains unverified.

## Supported path and artifacts

The Apple Silicon guide supports manual local inference with PaddlePaddle 3.2.1 or later. Its documented hardware validation covers the M4. The same guide offers an optional MLX-VLM recognition service (MLX-VLM ≥0.3.11) connected to the local document parser. The service performs region recognition and the PaddleOCR client still needs the layout detector and assembly stages. This attempt selected the simpler in-process PaddlePaddle route. [PaddleOCR Apple Silicon guide](https://www.paddleocr.ai/v3.7.0/en/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.html)

The official v1.6 configuration uses PP-DocLayoutV3 for page layout, region crops and ordering, and PaddleOCR-VL-1.6-0.9B for region recognition. PaddleX assembles the page result. The runner requests `device="cpu"` and the `native` recognition backend for both parts on the arm64 host. Layout is enabled. The already upright, flat fixture uses the pipeline defaults with orientation classification and unwarping disabled. On a successful run the harness records the actual predictor classes and device attributes, the detector boxes, parsed block rectangles and optional polygons, the block order, Markdown and timings. With preprocessing off, the expected coordinate mapping for this PNG is identity from the parser’s page pixels to the original 700 × 480 image. The PaddleX result source exposes `layout_det_res`, `block_bbox`, `block_polygon_points` and `block_order`. [PaddleX v1.6 configuration](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.0/paddlex/configs/pipelines/PaddleOCR-VL-1.6.yaml), [PaddleX result serializer](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.0/paddlex/inference/pipelines/paddleocr_vl/result.py)

| Artifact | Exact target | License |
|---|---|---|
| PaddlePaddle native CPU | `3.2.1`, PyPI wheel `cp312-cp312-macosx_11_0_arm64`, 99,491,247 bytes | [Apache 2.0](https://github.com/PaddlePaddle/Paddle/blob/v3.2.1/LICENSE) |
| PaddleOCR with `doc-parser` extra | `3.7.0` | [Apache 2.0](https://github.com/PaddlePaddle/PaddleOCR/blob/v3.7.0/LICENSE) |
| PaddleX | `3.7.0` | [Apache 2.0](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.0/LICENSE) |
| Layout weights | `PaddlePaddle/PP-DocLayoutV3@241f8bdfc77a7c7bee915a5057aaee58c235a8d3`, 130,806,572 bytes, SHA-256 `70bd316b0582769ec968829fd1feb1a6a58b7c941b938327e551b6b12b45c137` | [Apache 2.0](https://huggingface.co/PaddlePaddle/PP-DocLayoutV3/tree/241f8bdfc77a7c7bee915a5057aaee58c235a8d3) |
| Recognition weights | `PaddlePaddle/PaddleOCR-VL-1.6@132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd`, 1,917,255,968 bytes, SHA-256 `85a479d506a11e724e7285d395c551be69f41dbc16b6342d3cacfb189aed71db` | [Apache 2.0](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/tree/132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd) |

The arm64 CPython 3.12.16 environment and downloads are scoped to `.vt-state/paddle` in worktree `vt-vision`. Hardware was Apple M3 Max with 48 GiB of unified memory, 744 GiB reported free disk at launch, macOS `27.2.1` (`26B131`), and 87–89% reported system-wide memory free during the attempt. No service or external inference endpoint was used. Actual heavyweight inference is guarded by POSIX `flock` on `/Users/vakof/orca/vision-to-tables/.vt-state/inference.lock`.

## Executed commands and results

The fixture source is [`scripts/paddle-gate1-invoice.svg`](../../scripts/paddle-gate1-invoice.svg). The runner renders it with macOS `sips` and writes `.vt-state/paddle/output/invoice.png`. The rendered page was visually checked. It has two synthetic line items, a bilingual heading and a printed total of `10,00 €`. PNG SHA-256 was `0c9817ab13eddba0747ecd6d661bb4b9a8f9dbe004a2bf53bd9ff236ef4b5428`.

```sh
UV_CACHE_DIR="$PWD/.vt-state/paddle/uv-cache" uv venv --python python3.12 .vt-state/paddle/env
UV_CACHE_DIR="$PWD/.vt-state/paddle/uv-cache" uv pip install --python .vt-state/paddle/env/bin/python \
  'paddlepaddle==3.2.1' 'paddleocr[doc-parser]==3.7.0' 'paddlex==3.7.0'
curl -fsSL --retry 2 --max-time 180 -o .vt-state/paddle/models/layout/inference.pdiparams \
  'https://huggingface.co/PaddlePaddle/PP-DocLayoutV3/resolve/241f8bdfc77a7c7bee915a5057aaee58c235a8d3/inference.pdiparams'
curl -fsSL --retry 2 --max-time 300 -o .vt-state/paddle/models/vlm/model.safetensors \
  'https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/resolve/132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd/model.safetensors'
```

The exact pins resolved 100 packages in `5m 02s`. The package installer was still downloading wheels when the feasibility capture ended. The layout curl hit timeout `28` after `179615 ms` with `9530610 of 130806572 bytes received`, then retried. The worker stopped its three provisioning processes after collecting the transfer probes, preserving the partial files (4,550,656 layout and 12,091,392 recognizer bytes). The installer exited `143` on that controlled stop. `uv pip list` reported no installed packages in this environment.

```sh
/usr/bin/time -p curl -fLsS --max-time 20 --range 0-16777215 -o /dev/null \
  -w 'HTTP %{http_code}\nbytes %{size_download}\nbytes_per_second %{speed_download}\n' \
  'https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6/resolve/132c45f52c0a26ed5a6b2f6bd8e3985794cc27dd/model.safetensors?download=true'
/usr/bin/time -p curl -fLsS --max-time 20 --range 0-16777215 -o /dev/null \
  -w 'HTTP %{http_code}\nbytes %{size_download}\nbytes_per_second %{speed_download}\n' \
  'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PaddleOCR-VL-1.6_infer.tar'
```

| Upstream | HTTP | Transfer | Wall time | Exit |
|---|---:|---:|---:|---:|
| Hugging Face | 206 | 585,957 bytes at 29,284 B/s | 20.02 s | 28 |
| PaddleX object storage | 206 | 539,938 bytes at 26,992 B/s | 20.01 s | 28 |

The Hugging Face probe implies roughly 18 hours for a single connection at its measured rate. This is a local provisioning throughput blocker. Runtime compatibility on the M3 Max remains untested.

```sh
/usr/bin/time -l sandbox-exec -p '(version 1) (allow default) (deny network*)' \
  .vt-state/paddle/env/bin/python scripts/paddle-gate1.py
```

```text
Model provisioning incomplete
layout/inference.pdiparams: have 4550656 of 130806572 bytes
vlm/model.safetensors: have 12091392 of 1917255968 bytes
        0.08 real         0.06 user         0.01 sys
            25968640  maximum resident set size
exit=1
```

The reported wall time and peak RSS cover fixture generation and the weight preflight. The same sandbox policy blocked a test `curl -fsSI --max-time 3 https://huggingface.co` with exit `6`, `Could not resolve host`. No runtime predictions or boxes were available to check.

## Resume and verify

On the same worktree the provisioner reuses the isolated env and UV cache and requests HTTP resume for partial weights. The commands for packages, detector files and recognizer files are independent. Run the offline harness once all three finish.

```sh
sh scripts/paddle-gate1-provision.sh packages
sh scripts/paddle-gate1-provision.sh layout
sh scripts/paddle-gate1-provision.sh vlm
/usr/bin/time -l sandbox-exec -p '(version 1) (allow default) (deny network*)' \
  .vt-state/paddle/env/bin/python scripts/paddle-gate1.py
```

The harness checks both artifact sizes and SHA-256 digests. The expected successful outputs are `.vt-state/paddle/output/{cold,warm}/` with per-page upstream JSON and Markdown and `.vt-state/paddle/output/report.json` with runtime measurements and component identities. Verify ordered parsed blocks and original-pixel boxes against the generated invoice before enabling the parser route. Shell syntax, Python compilation and Git whitespace checks passed during this feasibility stage.
