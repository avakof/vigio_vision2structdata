# Independent review of the integrated commit

| | |
|---|---|
| Reviewed commit | `dfa12df8304ab4753369a2ef4aa03ac90386db1f` (`main` = `vt-domain` = `vt-review` at review start, 2026-11-25) |
| Reviewer | Claude Code, Opus 5.5 / high, worker task `task_0c28b0db7e0f` |
| Read | `docs/development/REQUEST.md`, `docs/development/interfaces.md`, `freeze.md`, `contracts.md`, README, ARCHITECTURE, `src/vision_tables/**`, evidence under `evidence/development` |
| Environment | `uv sync --locked --python 3.12.16` in the review worktree; no model weights loaded; no network inference; no private documents |
| Probes | [`evidence/development/reviewer/test_review_probes.py`](../../evidence/development/reviewer/test_review_probes.py); results in [`results.txt`](../../evidence/development/reviewer/results.txt) |

The probes run the production ingestion, vision adapter, domain assembly and corrections, SQLite store and exports. They replace only the model subprocess (`runtime_local.call`) and the readiness check (`models.runtime_info`). Each probe asserts the required behaviour, so a failing probe reproduces a finding and should pass after a fix.

## Commands and results

| Command | Result |
|---|---|
| `.venv/bin/python -m pytest -q` | 213 passed |
| `.venv/bin/python -m pytest -q evidence/development/reviewer/test_review_probes.py` | 7 failed, 5 passed. Every failure reproduces a finding below. |
| `vision-tables evaluate fixtures/manifest.json --output … --state-dir <empty>` | exit 0, `completed`, with 10 of 15 cases `missing_model` (finding L2) |

## Findings

Severity: **High** blocks the correction or review workflow, or risks data loss on a common path. **Medium** means a wrong or silently incomplete result, or a misleading claim, on a plausible path. **Low** covers robustness, provenance or reporting gaps.

### H1 · Correcting an unreadable, absent or disputed field crashes with an internal error

- Location: `src/vision_tables/domain/corrections.py:127` (`_human`: `old.raw[:400]`).
- Cause: the vision adapter gives `raw=None` to every field it could not read or ground (`vision.py` `_read_field`) and to every disputed reread (`_merge`). `not_present` observations also have `raw=None`. Correcting any of these evaluates `None[:400]`.
- Reproduction: `test_unreadable_field_can_be_corrected` and `test_resolved_reread_disagreement_can_be_approved`. `review apply` exits **1**, `internal_error`, `TypeError: 'NoneType' object is not subscriptable`. Nothing is stored.
- Impact: the review workflow cannot correct the fields that most need review. The existing tests missed this because the store and CLI tests use `fake_apply` and the domain tests use observations whose `raw` is set (see L4).

### H2 · Carried adapter and ingestion issues block approval permanently, and reprocessing cannot clear them

- Location: `domain/corrections.py:34` (`_carried`) and `:70` (`review_gate`); `domain/catalog.py` (only `invalid_evidence` is `OBSERVATION_BOUND`). Producers of the review-severity issues: `vision_ingest.py:84` (`low_contrast`, `low_resolution`), `:225` (`pdf_text_partial` / `pdf_text_unreliable`), `vision.py:407,416,418,436` (`unsupported_quote`, `reread_*disagreement`), and others.
- Cause: issues that are not in the catalog default to severity `review`. They are never derived and are carried into every correction version. Reprocessing with the same configuration returns the same blocked version (`store.py:385`, `action: reused`).
- Reproduction:
  - `test_low_contrast_capture_can_be_approved_after_review`: approve exits 4 with `approval_blocked: low_contrast`, and a reimport gives `reused v1`.
  - `test_reread_disagreement_blocker_retires_once_the_field_is_corrected`: this probe works around H1. After the disputed field is corrected, approval is still blocked by `reread_disagreement`.
- Impact: human approval can never be reached for faint captures, any PDF with a ≥15 % raster or hidden OCR text layer, or any page whose rereads disagreed or produced an unsupported quote. Only a configuration change escapes the state. The bundle's resolution text, "Resolve the adapter issue or reprocess", cannot be acted on.

### M1 · Corrections are accepted on a superseded provisional extraction, and the human edits are orphaned

- Location: `store.py:476-485`. The stale check compares against the head of the base's own lineage only. The `extractions` key now points to the superseding lineage (`store.py:385-405`).
- Reproduction: `test_correction_on_superseded_provisional_extraction_is_rejected`.
  1. A truncated run gives v1 (incomplete, provisional).
  2. A complete rerun with the same configuration gives v2 (`superseded`).
  3. Corrections with `base_version: 1` are accepted (exit 0, v3).
  4. A reimport serves **v2**, without the edits, while `documents show` reports v3 as latest.
- Impact: this breaks the replay rule that reimport returns the reviewed descendant and preserves human edits. Approval or edits made on v3 are invisible to reimport and batch consumers.

### M2 · A pending export of a corrected version is lost after any single-format export to the same directory

- Location: `cli.py:217-219` (`_export_all`) and `store.py:566-585` (`register_export` merges the formats, then `mark_export` marks the row published). `exports.publish` writes only the requested formats.
- Reproduction: `test_pending_correction_export_survives_a_later_single_format_export`.
  1. `review apply --output out` hits a publication failure (exit 8, `exports` row pending `json,csv,review`).
  2. `export --version 2 --format sqlite --output out` succeeds.
  3. `jobs recover` reports `ok`, and `out/<doc>/v0002` contains only `document.sqlite3` and `manifest.json`.
- Impact: JSON, CSV and the review bundle for that version are silently never published, while recovery reports success. Extraction jobs escape this because job recovery republishes the default formats (`test_pending_default_export_survives…` passes). Correction-created and manual exports have no such fallback.

### M3 · Native-text PDF pages are read from text alone, so smaller raster or drawn content is dropped without a coverage signal

- Location: `vision_ingest.py:166-171` (`mixed` only for images covering ≥15 % of the page; vector paths, annotations and form XObjects are not considered) and `vision.py:390` (`native_only` sends no image to the model).
- Reproduction: `test_native_pdf_with_raster_note_is_not_read_text_only_without_a_signal`. The probe builds a synthetic PDF with a native-text invoice and a JPEG note `REMISE -5,00` covering 5 % of the page. The page is `text_layer: usable`, the model request has `image: None`, and extraction exits **0** `completed` with validation `valid`. The only issue is `critical_missing` (info).
- Impact: stamps, pasted scans, handwritten annotations and drawn corrections in digital PDFs are silently omitted while coverage reports the page as fully processed. Request §5 asks for rendered images where the text layer is incomplete, and asks that completeness never be implied.

### M4 · The correction format cannot add a missed field or row, or remove a spurious row

- Location: `contracts.py` `Correction` / `CorrectionSet` (only `observation_id` edits), `domain/corrections.py` (`unknown observation` is rejected).
- Impact: when the model omits a printed line or the total, the reviewer cannot record the printed fact. A `total_mismatch` can then be cleared only by editing an observed amount to a value that is not printed. A hallucinated row can only have each field set to `not_present`, and it stays as an entity in every table. Request §9 asks that reviewer-supplied changes be recorded. This is a contract gap, not a crash, and was not probed beyond schema inspection.

### M5 · The handoff and acceptance evidence the documentation promises does not exist at this commit

- Location: `README.md:30` and `docs/ARCHITECTURE.md:23` link `docs/HANDOFF.md` and describe "verified offline commands, the measured route status and the exact sample sizes". The file is absent. No machine-readable acceptance report or gate status file exists. `evidence/development/vision-paddle-gate1.md` records the hybrid route as blocked at provisioning, and `qa-fixtures.md` records production evaluation, accuracy and offline acceptance as `not_run`.
- Impact: the top-level documentation implies measured and verified results that the repository does not contain. Request §12–13 requires `passed/failed/blocked/not_run` for each gate. The evidence documents themselves are honest; only the README and ARCHITECTURE wording overstate them.

### L1 · A batch that skips inputs reports `completed`, exit 0

- `store_jobs.py:378`. `test_unsupported_files_in_batch_are_visible`: a directory with `a.png` and `scan.tiff` gives `completed 0 {'completed': 1, 'skipped': 1}`. The skip is visible per item but not in the status or exit code.

### L2 · `evaluate` exits 0 `completed` when the runtime is missing, and the report does not identify the model

- `cli.py:321`, `evaluate.py:564`. With an unprovisioned state directory, 10 of 15 cases are `missing_model`. The command still exits 0, and the synthetic development stratum shows `case_pass 0.556`, earned only by the bounded-failure fixtures. The top-level `configuration` contains only budgets and route, with no model, revision, readiness or fingerprint. Coverage 0.0 is reported, so this is a presentation risk, not a hidden zero.

### L3 · Field evidence does not identify the attempt or the reading channel

- `domain/build.py:151,167`. `Evidence.attempt_id` is never filled. `channel` is `direct` for every direct-route field, including native-text-only readings where the model saw no image, and `parser` for every hybrid field. After rereads, crops and merges, a value cannot be traced to the model attempt that produced it. The attempts are listed only in `run.calls`.

### L4 · No test exercises the integrated pipeline, domain, store and corrections path

- `tests/test_store_support.py` states that the store and CLI tests use a stand-in domain (`fake_apply`) and hand-built documents. No repository test runs `pipeline.extract` → `domain.assemble` → `Store.persist_candidates` → `domain.apply_corrections`. H1 and H2 sit exactly on that seam. The reviewer probe `test_integrated_replay_correction_and_consistent_exports` is a starting point.

## Verified without a finding

- **Replay:** the same file and configuration give `reused` with no new version. A correction gives v2. An identical corrections file replays as `replayed v2`. A reimport afterwards serves v2. A changed-config run gives `new_extraction`. Identical printed rows stay distinct (`duplicate_row` is info).
- **Truncation:** a valid JSON reply at the token limit gives `incomplete`, exit 3, with `model_token_limit`.
- **Export consistency:** JSON, `observations.csv` and the standalone SQLite export hold the same observations, values and states for the corrected version. `=HYPERLINK…` and `@SUM…` are escaped in CSV and kept verbatim in canonical JSON.
- **Normalization:** French and English amounts, `1,234` (ambiguous with alternatives), `03/04/2026` (ambiguous; resolved only from another unambiguous date in the same document, with the reason recorded), `$` (ambiguous), imprecise quantities, and non-convertible units all behave correctly.
- **Local processing:** model subprocesses run under `sandbox-exec (deny network*)` with Hugging Face offline variables set. The repository test of real child-process network denial passes. Provisioning is the only networked path.
- **No-data failures:** these exit non-zero (`no_extraction` → 4). Incomplete coverage cannot be approved, a constraint the `Document` validator enforces.

## Unresolved limits of this review

- No real Qwen or Paddle inference was run. Route quality, latency, memory and the offline gate on real weights are unverified by this review. The hybrid route is blocked according to the repository's own evidence.
- The interruption and crash paths were not re-tested beyond the repository's subprocess tests, which pass.
- HEIC, multi-page grouping with skipped members and the menu and recipe derivations were reviewed by reading code only.
- Findings M4 and M5 come from inspection, not from a failing probe.

## Recheck procedure

After fixes, run `.venv/bin/python -m pytest -q` and `.venv/bin/python -m pytest -q evidence/development/reviewer/test_review_probes.py`. All 12 probes should pass. If H2 is fixed by changing policy rather than mechanism (for example, an explicit reviewer acknowledgement of carried issues), adjust the two H2 probes to the agreed contract.
