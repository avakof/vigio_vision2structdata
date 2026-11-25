# Local release handoff

The headless CLI, Python package, SQLite store and two offline inference routes ran on this Apple M3 Max with 48 GiB unified memory and macOS 27.2.1. Processing and review use local files. Every new extraction requires review. The frozen production commit is `bcffc67782a0b786531e6c09d4b5e7583ed63cc8`, pipeline 0.1.3. All six delivery gates passed. The integrated suite passed 371 checks, and the evaluation covered 25 cases on each route, for 50 case runs. [Acceptance and gate evidence](../evidence/acceptance.json) · [Detailed performance](EVALUATION.md) · [Build and tests](../evidence/verification/software.json)

## Results on real documents

| Cohort | Direct exact fields | Hybrid exact fields | Direct answerable coverage | Hybrid answerable coverage |
|---|---:|---:|---:|---:|
| Three archival development pages | 1/8 | 3/8 | 1/7 | 2/7 |
| Four archival held-out pages | 12/22 | 11/22 | 12/20 | 12/20 |
| Three additional handwritten recipe crops | 4/18 | 6/18 | 3/14 | 5/14 |

Both routes correctly linked the four labeled dishes to their four prices on the held-out printed menu, even as the document remained incomplete. On the English handwriting diagnostics, hybrid retained the active butter quantity as 3 and omitted its unit. Both missed the separately crossed-out reading, the labeled butter range and the uncertain sugar row. All outputs required review. Zero automatic acceptances leave the false-acceptance rate among automatically accepted documents undefined. [Independent QA](../evidence/development/qa-release.md) · [Handwriting cases and limitations](../evidence/evaluation/handwriting-diagnostics/README.md)

Direct is the default. Both routes matched the development invoice total and missed the two labeled development rows. The additional hybrid matches were the printed menu venue and corroborated absence of recipe yield. The direct development trial ran in 383.38 s versus 589.59 s hybrid and used a lower peak child RSS. Settings and route choice were fixed before held-out inference. The frozen repeat took 494.88 s direct and 555.81 s hybrid and reproduced all development case scores. [Selection record](development/evaluation-settings.md) · [Paired development results](../evidence/evaluation/release/development-comparison.json) · [Paired held-out results](../evidence/evaluation/release/heldout-comparison.json)

## Installation and processing

Run from the repository root. Provisioning verifies the pinned model files and installs the isolated runtimes. The core uses CPython 3.12.16. Project weights occupy about 5.6 GiB for Qwen and 1.9 GiB for the two Paddle components. The two model environments, caches, job artifacts and exports require additional disk space.

```sh
export VT_STATE_DIR="$PWD/.vt-state"
export VT_INFERENCE_LOCK="$VT_STATE_DIR/inference.lock"
export UV_CACHE_DIR="$VT_STATE_DIR/uv-cache"
uv sync --locked --python 3.12.16
.venv/bin/vision-tables models prepare --pipeline direct
.venv/bin/vision-tables models prepare --pipeline hybrid
.venv/bin/vision-tables doctor --pipeline hybrid
.venv/bin/python tools/verify_offline.py extract fixtures/feasibility/invoice.png --type invoice --pipeline direct --output output/photo
.venv/bin/python tools/verify_offline.py extract fixtures/synthetic/invoice-fr-mixed.pdf --type invoice --page-group 1,2 --pipeline direct --output output/pdf
```

The process-scoped verifier checks that a loopback exchange succeeds and an external connection receives an OS denial. The CLI also isolates model child processes during ordinary extraction. The PDF command groups pages 1 and 2 as one document. Ungrouped pages become separate documents. A nonzero extraction exit can carry a version with useful fields and incomplete coverage. Each CLI result lists the versions, job artifacts and review issues. [CLI options and exit codes](CLI.md)

```sh
.venv/bin/vision-tables documents list
.venv/bin/vision-tables jobs list
.venv/bin/vision-tables jobs recover
.venv/bin/vision-tables batch inbox --output output/batch
```

The grouped PDF command returned one reviewable invoice covering both pages, with 27 entities and 143 observations in 107.83 s. Its issues included a subtotal mismatch and an unsupported source quote. The exported SQLite database passed integrity and foreign-key checks. [Grouped PDF receipt](../evidence/verification/offline-grouped-pdf.json)

## Review, corrections and export

Use the document ID and version from the extraction response. Open the source and page or crop references in the review bundle. Set the reviewer, base version, edit targets and reasons in a separate corrections file. The example below uses the checked-in synthetic invoice's original hybrid extraction at version 2. For a fresh database, use the ID and base version actually printed by the CLI and adapt the example's targets to that snapshot.

```sh
DOC=doc-5f704466a44dd696b8317362adec39ec
.venv/bin/vision-tables review bundle --document "$DOC" --version 2 --output output/review
.venv/bin/vision-tables review apply evidence/examples/invoice/corrections.json --document "$DOC" --output output/review
.venv/bin/vision-tables export --document "$DOC" --version 3 --format all --output output/tables
```

The early recorded direct extraction read the reference, supplier, currency and amount; hybrid retained the reference, supplier and amount but produced two header records and omitted the separate currency field. The example correction removed the duplicate root and supplied the printed currency. It left the document awaiting review. Repeating the correction returned version 3, and real same-configuration reimport using the installed wheel also selected version 3. JSON, CSV and standalone SQLite were checked against that version. The immutable original and intermediate page/attempt files resolve against the local job directory named in the review bundle. The checked-in input is available independently. [Versioned examples](../evidence/examples/invoice) · [Source photo](../fixtures/feasibility/invoice.png) · [Preservation and replay checks](../evidence/examples/invoice/verification.json)

The frozen candidate created versions 4 and 5 on the same invoice. Both routes read VTX-4187, EUR and 37.25 and retained one header. The process-restricted CLI took 12.01 s direct and 40.87 s hybrid. A controlled fixture check confirmed the printed EUR in version 5 through a corrections file, creating version 6 with attribution and no human approval. Exact replay and a fresh hybrid reimport returned version 6. Hashes and observation values agreed across all three frozen JSON, CSV and SQLite exports. [Frozen receipts and commands](../evidence/examples/release-invoice/verification.json) · [Frozen snapshots and corrections](../evidence/examples/release-invoice)

## Runtime and scope

The direct runtime uses the pinned Qwen3.5-9B MLX 4-bit conversion, MLX-VLM 0.8.1, constrained decoding and independent validation. The still-image processor also needs torch and torchvision. The hybrid route runs PP-DocLayoutV3 and the full PaddleOCR-VL-1.6 recognizer on the CPU before constrained Qwen extraction. Both routes share an exclusive inference lock. Normal application calls start fresh model processes; the separate feasibility measurements include a second call with resident weights. [Component manifests and dependency locks](../src/vision_tables/assets) · [Feasibility measurements](../evidence/feasibility/README.md)

| One-page feasibility measurement | Qwen direct | Native Paddle parser |
|---|---:|---:|
| Load or initialize | 5.01 s | 10.04 s |
| First prediction | 4.86 s | 15.84 s |
| Second prediction in the same process | 1.10 s | 15.71 s |
| Output | 3/3 specified invoice fields twice | 6 layout boxes and 4 decoded blocks twice |

The two feasibility fixtures have different dimensions and content. Paddle mapped the printed 10,00 € amount to original pixels [380, 358, 631, 391] on its 700 × 480 source. A separate real run at 32 tokens per recognized crop recorded two exhausted crops. On the same 800 × 420 invoice, the first end-to-end production CLI comparison took 8.92 s direct and 37.38 s hybrid including lock wait. Both retained the observed amount 37.25. [Paddle results](../evidence/feasibility/paddle) · [Direct CLI](../evidence/verification/offline-direct.json) · [Hybrid CLI](../evidence/verification/offline-hybrid.json)

## Verification and ownership

The six specialists used separate Orca sessions, with a maximum of three active assignments alongside the lead. The independent Astra architect audited the draft and the later integrated deltas. Opus reproduced material failures on dfa12df and rechecked their fixes on 36b3200. The corrections, superseded-lineage handling, export format tracking, mixed-PDF routing and structural edits have executable regression probes. Two supplemental Sonnet assignments hit the first-party account limit after the original implementations had finished. The lead integrated their partial changes and commissioned independent rechecks. Multi-gigabyte transfers delayed genuine Gate 1 results, so contract freeze and implementation preceded those results. [Roster and launch receipts](../evidence/development/roster.json) · [Actual timing](development/timeline.md) · [Opus recheck](development/review-recheck.md) · [Architect delta recheck](development/final-delta-recheck.md)

The application supports JPEG, PNG, verified static HEIC and multipage PDF. Geometry is in original image pixels or PDF page points. Ungrounded fields retain a page reference. The domain layer uses Decimal or exact fractions where applicable and keeps raw text, alternatives and evidence. Correcting a fact recalculates its dependents. A clipped image can still omit facts beyond the frame; complete processing does not establish the original capture extent. [Architecture](ARCHITECTURE.md) · [Domain normalization and review rules](development/domain.md) · [Checked-in schemas](../schemas)

The final Opus review checked the frozen invoice attempts against actual Qwen replies, refetched the stored version lineage and independently confirmed the JSON, CSV and SQLite evidence. Astra checked 526 possible response cuts and preserved disputed prices, occurrence counts and native citations through SQLite. [Final Opus review and E1 closure](development/reviewer-release.md) · [Architect closure](development/recovery-parser-closure.md)

## Reproduce the comparison

Each command below runs fresh inference for every case. Use distinct output directories when retaining earlier reports. The strict scorer separates development, held-out, diagnostic and synthetic results. It records absent denominators explicitly. Matching source hashes, labels, per-case overrides and shared model settings are checked before creating a paired summary.

```sh
for route in direct hybrid
do
  .venv/bin/python tools/verify_offline.py evaluate fixtures/public/manifest.json --pipeline "$route" --output "output/evaluation/$route-development"
  .venv/bin/python tools/verify_offline.py evaluate fixtures/archive-heldout/manifest.json --pipeline "$route" --output "output/evaluation/$route-heldout"
  .venv/bin/python tools/verify_offline.py evaluate fixtures/handwriting-diagnostics/manifest.json --pipeline "$route" --output "output/evaluation/$route-diagnostic"
  .venv/bin/python tools/verify_offline.py evaluate fixtures/manifest.json --pipeline "$route" --output "output/evaluation/$route-synthetic"
done
.venv/bin/python tools/compare_evaluations.py output/evaluation/direct-heldout/evaluation.json output/evaluation/hybrid-heldout/evaluation.json
.venv/bin/python -m pytest -q tests evidence/development/reviewer
PYTHONPATH=src .venv/bin/python evidence/development/reasoner_recovery_probes.py
```

The JSON reports include per-fixture scores, source and label digests, processed and skipped pages, extra calls, token counts and RSS. The evaluation command's exit status describes whether it produced the report. Per-case status and business-field scores describe what the application actually extracted. The final evaluation gives the denominators by document family, language and writing style. [Evaluation and timings](EVALUATION.md) · [Checks on all 50 case runs](../evidence/verification/campaign.json)
