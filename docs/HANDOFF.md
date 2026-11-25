# Local release handoff

The application is on `main` in the independent vision-to-tables repository. The machine-readable [acceptance report](../evidence/acceptance.json) records gate states, supporting evidence and limitations. The first integrated review covered `dfa12df8304ab4753369a2ef4aa03ac90386db1f`. The [review findings](development/review.md) and [independent QA report](../evidence/development/qa-integrated.md) include executable reproductions.

## Run from the checkout

```sh
export VT_STATE_DIR="$PWD/.vt-state"
UV_CACHE_DIR="$VT_STATE_DIR/uv-cache" uv sync --locked --python 3.12.16
.venv/bin/vision-tables doctor
.venv/bin/vision-tables models prepare --pipeline direct
.venv/bin/vision-tables models prepare --pipeline hybrid
```

The last two commands explicitly provision components. The application verifies pinned weights against upstream digests. The direct environment uses MLX for inference; the Qwen3VL image processor also requires torch and torchvision. The hybrid environment has its own PaddlePaddle, PaddleOCR and PaddleX versions. Its local full-parser smoke must produce genuine layout boxes and recognized blocks before the route is ready. Installed host packages and model locks are described in the [component inventory](../src/vision_tables/assets).

```sh
.venv/bin/vision-tables extract fixtures/feasibility/invoice.png --type invoice --pipeline direct --output output/photo
.venv/bin/vision-tables extract fixtures/synthetic/invoice-fr-mixed.pdf --type invoice --page-group 1,2 --pipeline direct --output output/pdf
.venv/bin/vision-tables batch fixtures/synthetic --output output/batch
.venv/bin/vision-tables documents list
.venv/bin/vision-tables jobs recover
```

Set `DOC` to the `documents[].document_id` printed by a successful extraction. The following commands produce a review bundle, apply a reviewer-authored corrections file and export a version. Review the original, page images, grounded crop references, raw readings, alternatives and issue codes in the bundle. Set the reviewer and base version in the corrections file. [Correction examples and active-cell escaping](CLI.md)

```sh
.venv/bin/vision-tables review bundle --document "$DOC" --output output/review
.venv/bin/vision-tables review apply corrections.json --document "$DOC" --output output/review
.venv/bin/vision-tables export --document "$DOC" --version 1 --format json --output output/original
.venv/bin/vision-tables export --document "$DOC" --format csv --output output/tables
```

The process-scoped verifier tests loopback and an external socket before entering the CLI. Run separate development and held-out reports for each available route. The synthetic regression set has its own manifest.

```sh
.venv/bin/python tools/verify_offline.py extract fixtures/feasibility/invoice.png --type invoice --pipeline direct --output output/offline
.venv/bin/python tools/verify_offline.py evaluate fixtures/public/manifest.json --pipeline direct --output output/eval/direct-development
.venv/bin/python tools/verify_offline.py evaluate fixtures/archive-heldout/manifest.json --pipeline direct --output output/eval/direct-heldout
.venv/bin/python tools/verify_offline.py evaluate fixtures/public/manifest.json --pipeline hybrid --output output/eval/hybrid-development
.venv/bin/python tools/verify_offline.py evaluate fixtures/archive-heldout/manifest.json --pipeline hybrid --output output/eval/hybrid-heldout
.venv/bin/vision-tables evaluate fixtures/manifest.json --pipeline direct --output output/eval/synthetic
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q evidence/development/reviewer/test_review_probes.py
```

## Coordination and verification

The [roster](../evidence/development/roster.json) records requested and observed harness models and efforts, harness session IDs, worktrees, tasks and dispatches. All six specialists and the lead ran locally in Orca on the M3 Max; development harnesses used their configured providers. The production application reads documents locally. Concurrency stayed within three active specialist assignments. Heavy model calls share the common project lock. [Actual gate timing](development/timeline.md)

The archived development and held-out sources have independent, sparse human reference labels. Score them separately from generated fixtures. Accuracy, coverage and review rates need their denominators. Historical material and a small English held-out cohort limit generalization. [Development sources](../fixtures/public/SOURCES.md) · [Held-out sources](../fixtures/archive-heldout/SOURCES.md)

## Verification checkpoint

At the first integrated checkpoint 213 package tests passed. The six real negative CLI cases passed under OS-level external denial, including encrypted PDFs, malformed files and explicit runtime errors. Independent tests reproduced correction, publication and coverage defects. Opus verified the fixes on 36b3200 with all 294 package tests and 16 reviewer probes passing. [Recheck report](development/review-recheck.md) The static HEIC decoder and offline Qwen processor were exercised on this Mac. The model weight transfers and end-to-end inference measurements are pending at this checkpoint. The direct route is the configured baseline. No automatic acceptance threshold has been calibrated.
