# vision-tables CLI

Every command prints **one JSON object on stdout** (with `exit_code` inside it) and, unless `--no-progress`
is given, JSON progress lines on stderr (`job_started`, `progress`, `persisted`, `item_started`).
Document text never selects a path, query, URL or command: paths come from the command line and from the
process-generated job id.

## State and output paths

| Option | Meaning |
|---|---|
| `--state-dir DIR` (any position) or `VT_STATE_DIR` | Database `vision-tables.sqlite3`, per-job artifact roots `jobs/<job_id>/`, models, locks. Without either: `<checkout>/.vt-state` for an editable install, `~/.local/share/vision-to-tables` for a wheel. |
| `--output DIR` | Where exports are published (required by `extract` and `batch`; default `<state>/exports` elsewhere). |

Published layout, atomic per format, `manifest.json` last (its absence means an unfinished publication):

```
<output>/<document_id>/v0002/
  document.json              canonical snapshot, byte-identical to the stored row
  tables/documents.csv pages.csv regions.csv entities.csv observations.csv evidence.csv issues.csv corrections.csv
  tables/table_<kind>.csv    one wide table per entity kind present (line_item, menu_item, ingredient, ...)
  review/review.json corrections.schema.json corrections.example.json
  document.sqlite3           only with --format sqlite|all
  manifest.json              snapshot sha256 and sha256 of every published file
```

`<output>/batch-<id>.status.json` holds the per-document batch status.

## Commands

```sh
vision-tables doctor                                   # exit 5 until the local model is provisioned
vision-tables models prepare [--pipeline direct]       # the only networked command

vision-tables extract invoice.pdf --type auto --pipeline direct --output out/ --state-dir .vt-state
vision-tables extract scan.pdf --type invoice --pipeline hybrid --output out/        # fails clearly if unavailable
vision-tables extract bundle.pdf --output out/ --page-group 1,2 --page-group 4-5     # explicit documents; page 3 alone
vision-tables batch inbox/ --output out/ [--retry-failed] [--retry-incomplete]       # re-run the same command to resume

vision-tables review bundle --document DOC [--version N] --output out/               # structured issues + references
vision-tables review apply corrections.json --document DOC --output out/             # new version; exports regenerated
vision-tables export --document DOC --format json|csv|review|sqlite|all [--version N] --output out/

vision-tables evaluate fixtures/manifest.json --output eval/ [--pipeline direct --max-tokens 2048]
vision-tables documents list | show DOC
vision-tables jobs list [--state STATE] | show JOB | recover
```

Settings flags on `doctor`, `models prepare`, `extract`, `batch`, `evaluate`: `--pipeline`, `--type`,
`--page-group PAGES` (repeatable; `1,3` or `2-4`, one-based, source order, groups disjoint, at most 1000
pages per group), `--max-bytes --max-pages --max-pixels --render-pixels --max-crops --max-tokens --retries`,
`--lock-file`. Only content-affecting settings enter the configuration hash, so changing a budget makes a new
extraction version while changing `--output` or `--state-dir` never does. Pages not in a group each become a
document; skipped members of a group stay in its identity and are reported in `coverage.source_skipped_pages`.

## Exit codes and status

| Code | `status` | Meaning |
|---|---|---|
| 0 | `completed` | Extracted, persisted and published. **Still `review_required: true`**: nothing is auto-approved. |
| 1 | `failed` | Internal error (`error.code` `internal_error`). The job row and artifacts remain. |
| 2 | `usage_error` | Bad flags or settings; nothing started. |
| 3 | `incomplete` | Persisted, but a page was skipped, truncated or unread (`coverage`, `validation: incomplete`). |
| 4 | `failed` | No usable data (e.g. `no_extraction`); job kept as evidence, no version was reserved. |
| 5 | `failed` / `not_ready` | Missing model or runtime (`missing_model`, `hybrid_unavailable`, ...). |
| 6 | `failed` | Conflict: `stale_base` correction or `lease_lost`. Nothing applied. |
| 7 | `failed` | Invalid input or reference (bad corrections file, unknown document/version/format). |
| 8 | `export_pending` | Committed, but publication failed; `jobs recover` retries. |
| 130 | `cancelled` | SIGINT/SIGTERM. No partial version; see `error.next`. |

`batch` returns the worst item: any failure 4, else incomplete 3, cancelled 130. `skipped` (unsupported
extension) is explicit per item, lists the names in `skipped_inputs` and makes the batch `incomplete` (exit 3)
because the directory was not fully read; a directory with **no** supported file returns `failed`, exit 7,
`error.code: no_supported_files`. A file that is not a regular
file, is a symlink, or exceeds `--max-bytes` is rejected before it is read (`source_too_large` / `invalid_input`,
item `failed`, exit 4) and is retried automatically on the next run once it is acceptable. Every error has `code`, `message`, `next` and,
for jobs, `artifact_root`.

Example `extract` result (abridged):

```json
{"command": "extract", "status": "completed", "exit_code": 0, "state_dir": "/…/.vt-state",
 "job": {"job_id": "3f…", "artifact_root": "/…/.vt-state/jobs/3f…"},
 "source": {"path": "/…/invoice.pdf", "sha256": "…"},
 "documents": [{"document_id": "doc-…", "version": 1, "base_version": 1, "parent_version": null,
   "action": "created", "doc_type": "invoice", "validation": "issues", "approval": "needs_review",
   "review_required": true, "issue_codes": ["total_mismatch"], "possible_duplicates": [],
   "coverage": {"scope": [1], "processed_pages": [1], "skipped_pages": [], "source_skipped_pages": [],
                "incomplete_pages": [], "suspected_capture": false, "source_page_count": 1},
   "export": {"state": "published", "directory": "out/doc-…/v0001", "manifest": "…/manifest.json"}}]}
```

`action`: `created` (new document), `new_extraction` (changed configuration), `superseded` (complete retry
replaced an unedited incomplete base), `reused` (same source, scope and configuration; `version` is the reviewed
descendant, so earlier human edits and approvals are preserved).

## Versions, identity and replay

* Document id = source SHA-256 + ordered page scope. Extraction key = (document id, `run.config_hash`).
  Snapshot key = (document id, version). Job ids are separate UUIDs.
* Reimport with the same key never adds a second base; it returns the reviewed head of that base's lineage.
  A changed model, schema, pipeline or budget adds a new extraction version whose `parent_version` is the
  previous latest version; earlier snapshots, including approved ones, remain.
* An incomplete base is provisional. A later complete retry under the same key replaces it only if no human
  edit exists on it; otherwise the reviewer's lineage wins.
* A correction targets `base_version` and succeeds only if that version is still the head of its lineage when
  the child is inserted (same transaction as the domain recomputation). Otherwise `stale_base` (exit 6).
  An identical correction file is recognised by the digest of its canonical contents **before** the stale check
  and returns the version it created (`status: replayed`).
* A correction whose base descends from an extraction that a complete retry has **superseded** is rejected
  as `stale_base` and names the live version to review, so reviewer edits can never be orphaned from the
  version that reimport serves. Lineages of older, different-configuration extractions (for example an
  approved one) stay reviewable.
* Each export location tracks the formats it is *required* to hold (only grows) and those *published*
  (migration 002). It is `published` only when required ⊆ published, so a later single-format
  `export --format sqlite` can never discharge a pending JSON/CSV/review obligation; `jobs recover` publishes
  every required format.
* Snapshots, materialized observations, evidence links and issues are immutable (database triggers).
* Possible duplicates (different source bytes; same invoice supplier and number, or ≥80% row-name overlap) are
  flagged in `possible_duplicates`/`review.json`. Nothing is merged or deleted.

## Corrections format

Strict JSON object (duplicate keys, trailing text, non-finite numbers, wrong types and files over 8 MiB are
rejected before anything is applied):

```json
{"document_id": "doc-…", "base_version": 2, "reviewer": "R. Chef", "approve": false,
 "changes": [{"observation_id": "o-p0", "value": "2.50", "raw": "2.50", "state": "readable",
              "reason": "digit confirmed on the page image"}]}
```

`value` must be `null` unless `state` is `readable`. Calculated observations cannot be edited; correct their
inputs and the domain module recomputes them. `"approve": true` with `"changes": []` approves an unchanged valid
version and fails while blocking issues remain. The schema ships in every review bundle.

## Review bundle

`review/review.json` lists each issue with its observations (raw text, value, state, alternatives, reason),
evidence quotes, the page image and region/crop references (resolved absolute path, `exists`, box, transform),
the source file with `sha256_verified` (read-only), version history, possible duplicates and the exact
`review apply` command. A reference that is missing or would leave the job's artifact root is reported
unresolved, never followed. The bundle is valid from any output directory because it embeds absolute paths
under `state_dir`.

## CSV safety

Cells that a spreadsheet could execute (leading `=`, `+`, `-`, `@`, tab or CR, also after whitespace) get a
leading apostrophe unless they are plain signed numbers such as `-12.50`. Only the CSV presentation changes;
`document.json` and SQLite keep the original text. UTF-8, `\n` line ends, no BOM.

## Recovery

`jobs recover` (also safe to run at any time) claims jobs whose owner died or whose lease expired, finishes a
staged result (`jobs/<id>/documents.json`) **without new inference**, and republishes every pending export.
A job interrupted during inference has no staged result: it is reported (`needs_rerun`) and `extract` is rerun.
`batch` resumes by rerunning the same command; completed and unchanged items are skipped, expired item leases
are reclaimed, and a live worker's item is never taken. The batch id hashes the input and output paths and the
effective extraction fingerprint (`pipeline.extraction_fingerprint(settings, models.runtime_info(settings))`,
computed once per batch, the same value extraction records as `run.config_hash`). Upgrading the model, runtime,
component, schema or pipeline version, or changing a content setting, therefore starts a new batch that
re-extracts every file as a new extraction version; returning to the old version resumes the old batch. The
`batch` result reports `fingerprint`.

## Known limits

Interrupted inference is restarted from the source, not from partial page readings. Corrections require the
domain module's `apply_corrections`. A lost job lease is detected at the transaction, not mid-inference. The
standalone `document.sqlite3` is logically but not byte-for-byte deterministic. A directory unit replaced
after damage has a short window without that directory; the manifest-last rule keeps the version marked
unfinished until the swap completes.
