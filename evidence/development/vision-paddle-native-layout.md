# Native PP-DocLayoutV3 feasibility

**2026-11-25 · passed for layout.** The pinned detector ran twice on the M3 Max with PaddlePaddle’s CPU device and the v1.6 parser’s installed layout configuration. This probe exercised the layout stage only. The complete parser’s recognition run is tracked separately.

macOS 27.2.1 arm64, Python 3.12.16, PaddlePaddle 3.2.1, PaddleOCR 3.7.0, PaddleX 3.7.0, safetensors 0.8.0, NumPy 2.3.5, OpenCV 4.10.0.84 and Pillow 13.0.0. `uv pip check` reported 100 compatible packages. The actual predictor was `paddlex.inference.models.layout_analysis.predictor.LayoutAnalysisRunnerPredictor` on `cpu`. Paddle reported `cpu` and `is_compiled_with_cuda=False`.

- Layout revision `241f8bdfc77a7c7bee915a5057aaee58c235a8d3` and parameter SHA-256 `70bd316b0582769ec968829fd1feb1a6a58b7c941b938327e551b6b12b45c137`. The probe checked the digest before loading the model.
- The probe generated the Gate 1 synthetic 700 × 480 invoice with SHA-256 `0c9817ab13eddba0747ecd6d661bb4b9a8f9dbe004a2bf53bd9ff236ef4b5428`.
- The inference process acquired POSIX `flock` on `/Users/vakof/orca/vision-to-tables/.vt-state/inference.lock` in 0.010 s. The same sandbox profile allowed a loopback connection and rejected a fixed public connection with `EPERM`. No image or document data was sent by the network probe.

Imports took 2.028 s and detector initialization took 0.237 s. The first prediction in the process took **1.377 s** and reached 1,228,259,328 bytes peak RSS. The second took **1.001 s** and reached 1,240,858,624 bytes peak RSS. `/usr/bin/time -l` recorded 5.14 s wall time and 1,240,924,160 bytes maximum RSS for the full command, including both exports.

The cold and warm detector JSON and overlays match byte for byte. The boxes and polygon points use the source image's top-left pixel coordinates, with no transform between the fixture and detector inputs. The overlay confirms the title, table and total are spatially grounded. The detector classified the total as `vision_footnote` and emitted three overlapping text regions around the header. These are raw detector labels and overlaps before downstream parser filtering.

| Order | Label | Score | Source box `(x0, y0, x1, y1)` |
|---:|---|---:|---|
| 1 | `paragraph_title` | 0.7735 | `(44, 39, 359, 73)` |
| 2 | `text` | 0.4055 | `(44, 92, 265, 119)` |
| 3 | `text` | 0.4916 | `(43, 92, 304, 150)` |
| 4 | `text` | 0.4316 | `(44, 123, 304, 150)` |
| 5 | `table` | 0.9611 | `(46, 178, 653, 307)` |
| 6 | `vision_footnote` | 0.5336 | `(380, 358, 631, 391)` |

## Recognizer limits checked without loading its weights

The installed v1.6 `predict(max_new_tokens=...)` forwards the explicit cap to `get_layout_parsing_results`, `_paddleocr_vl_run_vl_recognition_batches`, and `DocVLMLocalPredictor.process`. The local predictor passes it to `PaddleOCRVLForConditionalGeneration.generate`; the generation mixin adds it to the input token count and can stop earlier at EOS. The installed local v1.6 recognizer uses batch size 1, so this bounds new tokens for each recognition crop. An unspecified cap in the pipeline becomes `4096`; the lower-level native direct predictor has a separate `8192` default. The public block JSON carries decoded content without a generated-token count or finish reason. The source confirms the explicit budget plumbing. It does not establish that all emitted block text is complete. [PaddleX v1.6 pipeline](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.0/paddlex/inference/pipelines/paddleocr_vl/pipeline.py), [native predictor](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.0/paddlex/inference/models/doc_vlm/predictor.py), [generation mixin](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.0/paddlex/inference/models/common/transformers/generation/utils.py)

The installed precision check returned `False` for bfloat16 on CPU; the predictor chooses float32 on that branch. The actual SigLIP resize helper enlarged the table crop from `129×607` to `168×700` (height × width). It also accepts short positive sides at `1×200`, rejects `1×201` with `ValueError` for aspect ratio, and rejects `0×10` by division by zero. The native predictor preprocesses a crop before calling `generate` and does not recover from those exceptions in that method. Current application subprocess failure handling records the failed page as incomplete. Actual per-crop token exhaustion, model memory and full-parser CPU behavior await the queued run. [Installed-version resize algorithm](https://github.com/PaddlePaddle/PaddleX/blob/v3.7.0/paddlex/inference/models/doc_vlm/processors/paddleocr_vl/_siglip.py)

## Reproduction

Run from this worktree with a fresh output directory. The probe uses the existing environment and detector in the main checkout.

```sh
project=/Users/vakof/orca/vision-to-tables
uv pip check --python "$project/.vt-state/paddle/env/bin/python"
/usr/bin/time -l /usr/bin/sandbox-exec -f "$project/tools/feasibility/offline.sb" \
  "$project/.vt-state/paddle/env/bin/python" -I -B scripts/paddle-layout-probe.py \
  "$project/.vt-state/paddle/models/layout" .vt-state/paddle/layout-probe-offline
```

The raw detector JSON, local overlays and `layout-report.json` are under `.vt-state/paddle/layout-probe-offline/`. The first attempt’s detector JSON was written successfully before PaddleX’s optional `save_to_img` tried to fetch `PingFang-SC-Regular.ttf`. The network sandbox denied that lookup. The probe now draws overlays with Pillow’s built-in font, requiring no downloaded font. The production adapter already consumes JSON.
