# vision-to-tables

A local command-line application for extracting evidence-backed invoice, menu and recipe data from JPEG, PNG, static HEIC and PDF sources. It stores the original, page geometry, local model attempts, field observations, corrections and immutable document versions. Each exported row links back to the source. French and English readings retain their original spelling. Static HEIC uses the native ImageIO decoder on macOS.

## Setup

From the repository root on an Apple Silicon Mac, install the locked Python core and inspect local components:

```sh
UV_CACHE_DIR="$PWD/.vt-state/uv-cache" uv sync --locked --python 3.12.16
export VT_STATE_DIR="$PWD/.vt-state"
.venv/bin/vision-tables doctor
.venv/bin/vision-tables models prepare
```

Model preparation needs network access and several gigabytes of disk. It verifies the pinned model artifact, installs the separate native runtime and writes a local component manifest. The core test suite can run before provisioning:

```sh
.venv/bin/python -m pytest -q
```

## Process documents

```sh
.venv/bin/vision-tables extract fixtures/feasibility/invoice.png --type invoice --pipeline direct --output output/invoice
.venv/bin/vision-tables extract fixtures/synthetic/invoice-fr-mixed.pdf --type invoice --pipeline direct --page-group 1,2 --output output/pdf
.venv/bin/vision-tables evaluate fixtures/public/manifest.json --output output/evaluation
```

Pages are separate by default. Use explicit page grouping for a multi-page document. Inspect review issues and evidence before approving a version. The release handoff includes grouped PDF, correction, replay and export commands, plus the verification status of each local model route. [Release handoff](docs/HANDOFF.md)

See the [CLI reference](docs/CLI.md), [architecture](docs/ARCHITECTURE.md), [JSON Schemas](schemas), [synthetic fixtures](fixtures/README.md) and [real-document sources and labeling limits](fixtures/public/SOURCES.md). [Measured accuracy and timings](docs/EVALUATION.md) · [Machine-readable acceptance](evidence/acceptance.json)

The [invoice photo trial](reports/invoice-photo-test-2026-11-25.md) reports field coverage, line-item counts and review findings from a local batch of 11 JPEGs.
