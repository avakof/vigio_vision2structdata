# Recheck of the review findings after the fixes at 36b3200

| | |
|---|---|
| Original review | `dfa12df8304ab4753369a2ef4aa03ac90386db1f` ([review.md](review.md)) |
| Rechecked commit | `36b3200aa93d8faa3d4e6eeda3b9edb66ec6d141`, merged into `vt-review` as `9fedab5` with no conflicts |
| Package tests | `.venv/bin/python -m pytest -q`: 294 passed |
| Reviewer probes | `.venv/bin/python -m pytest -q evidence/development/reviewer`: 16 passed (the 12 original probes plus 4 new recheck probes) |
| Evidence | [`recheck-results.txt`](../../evidence/development/reviewer/recheck-results.txt), [`test_recheck_probes.py`](../../evidence/development/reviewer/test_recheck_probes.py) |

I read the changes in `store.py`, `migrations.py` (002), `exports.py`, `cli.py`, `store_jobs.py`, `contracts.py`, `domain/{build,catalog,corrections,structure}.py`, `vision.py` and `vision_ingest.py`. Every probe again uses production ingestion, the adapter, domain, Store, CLI and exports. Only the model subprocess is faked.

## Item by item

| Finding | Status | How it was fixed | Rechecked with |
|---|---|---|---|
| **H1** crash on correcting fields with no raw text | **Fixed** | `_human` shows `no text` together with the prior candidates. | `test_unreadable_field_can_be_corrected`: exit 0. |
| **H2** permanent approval blockers | **Fixed** | The four capture-quality notices (`low_contrast`, `low_resolution`, `pdf_text_partial`, `pdf_text_unreliable`) are now `info`. `unsupported_quote` and `reread_disagreement` attach to the affected observations at assembly and drop to `info` once a human has supplied each of them. | Low-contrast approval exits 0. A reread disagreement is approvable after its field is corrected, both through the CLI and directly through the domain. |
| **M1** correction on a superseded provisional lineage | **Fixed** | `apply_corrections` returns `stale_base` when the base's extraction key now points at another version. | `test_correction_on_superseded_provisional_extraction_is_rejected`: exit 6. |
| **M2** pending export lost after a single-format export | **Fixed** | Migration 002 adds `published_formats`. A row is published only when every required format has been published. `register_export` never clears an obligation. | Both export probes pass. |
| **M3** native PDF read text-only despite visual marks | **Fixed** | Ingestion inventories images, paths (with only rules and light backgrounds treated as decoration), form XObjects and annotations. Any visual mark makes the page `partial`, so the page image is sent to the model. Content that cannot be represented adds an incomplete region. | A 5 % raster note and a 0.04 % raster both give `partial`, and the model receives the page image. |
| **M4** no structural corrections | **Fixed** | Typed `add_row`, `add_field` and `remove_row` operations, ordered and applied atomically before field changes. They are included in the replay digest and exported as `structural_corrections.csv`. | `test_m4_structural_add_remove_add_field_through_cli_store_replay_and_exports`: one CLI event removes an invented row, adds a missed row after an anchor and adds a missed field, then approves. Results: v2 `human_approved` and `total_mismatch` cleared. Replay returns v2, a reimport reuses v2 and v1 still contains the invented row. JSON, CSV wide and relational tables, and SQLite entities and observations agree. `test_m4_rejections_leave_no_version`: 7 invalid operations exit 7 and create no version. Reusing a removed id is rejected (exit 7) and a stale structural base gets exit 6. |
| **M5** missing handoff and gate states | **Fixed (provisional)** | `docs/HANDOFF.md` and `evidence/acceptance.json` exist. Gates: 0 passed, 1 blocked, 2 passed, 3 failed, 4 not_run, 5 failed. Routes are blocked, `offline_production` is not_run and `all_requested_routes_complete` is false. | Inspection. |
| **L1** batch with skipped inputs reported completed | **Fixed** | `incomplete`, exit 3, with `skipped_inputs` and a note. | `test_unsupported_files_in_batch_are_visible`. |
| **L2** evaluate succeeds without a runtime | **Fixed** | `status: blocked`, exit 5, `blocked_cases: 10`. The report configuration names the model, revision, `components_ready: false` and `base_config_hash`. | CLI run against an empty state directory. |
| **L3** evidence lacks attempt and channel | **Fixed** | Each field carries attempt-scoped evidence (`attempt_id`, and the channel taken from the cited region kind). Merged disagreements keep the evidence from both attempts. The `Document` validator rejects attempt ids that do not appear in `run.calls`. | `test_l3_field_evidence_names_attempt_and_channel`. |
| **L4** no integrated domain+store tests | **Fixed** | `tests/test_qa_integrated.py` drives the CLI with the real domain and Store, and fakes only the runtime. | Included in the 294 passing tests. |

## Remaining observations (not blocking)

- **Nit:** `README.md:30` still describes the handoff as containing "verified offline commands, the measured route status and the exact sample sizes". The handoff and `acceptance.json` correctly say that measurements and offline production are pending or not_run, so the README sentence should be softened until those measurements exist.
- **Documented behaviour:** if a human edits an incomplete (provisional) base, a later complete retry with the same configuration reuses the reviewer's lineage instead of replacing it (`store.py:383`, documented in `docs/CLI.md:104`). The way out is to reprocess with a changed configuration. This is acceptable as documented.
- **Design note:** issue binding for H2 matches the adapter's reason text (`catalog.FIELD_LEVEL`). If the adapter's wording changes without the catalog being updated, an issue will bind to no observations and drop to `info`. A shared constant or code would make the coupling explicit. Neither the tests nor this review found a current mismatch.

## Not yet checked

- Real Qwen and Paddle inference, route comparison, latency and memory, and the offline production gate are still pending. The coordinator will send the release measurements for an evidence-only follow-up.
- The crash and interruption paths of the new export bookkeeping are covered by the repository's tests. I did not re-run kill tests against migration 002.
