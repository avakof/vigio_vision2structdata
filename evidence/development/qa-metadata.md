# Independent evidence metadata QA

Reviewed `28b9cf6bda3b14a79970755fa6c61133382d095e`. The merge into `vt-qa`, `5d9409170668989f78fe1de8c7a82287aa0146a6`, had the same Git tree. Runtime replies were injected at the model boundary. Real PDF/image ingestion, crop creation, adapter validation, domain assembly, Store, corrections and exports ran on Python 3.12.16 and SQLite 3.53.1. No inference, model download or held-out prediction ran.

| Check | Status | Evidence |
| --- | --- | --- |
| Migration 002 to 003 | passed | A database using the 002 table definitions contained a real extraction, its reviewer correction, and a changed-config extraction. The latter reused the crop region ID with a different transform and artifact digest. Migration recovered each version's issue severities, defaulted an omitted historical severity to `review`, recovered each transform, and preserved exact snapshot bytes, snapshot hashes and crop hashes. Reopening was idempotent. SQLite rejected updates and deletions against immutable rows. |
| Fresh Store and all export formats | passed | Canonical and SQLite snapshots were byte-identical with matching hashes. Issue severity, observation source language, region language and writing, and crop transform and hash matched the JSON, CSV, main SQLite and standalone SQLite records. Review item severities and the resolved source and crop references matched the same version. A real correction changed the date value and retired its ambiguity issue while preserving its source language and evidence. |
| Writing style, language, geometry and source data | passed | Native print remained print despite a conflicting injected writing hint. Whole-page print and handwriting hints yielded a boxless mixed-style summary. The French language hint did not resolve the ambiguous date or the dollar currency. The adapter rejected extra proposed bounding-box and locale properties. The PDF directive remained a quoted source excerpt. The `=2+2` row remained literal in JSON and SQLite and was escaped in CSV. |
| Standalone SQLite lineage integrity | failed | A corrected standalone file failed `PRAGMA foreign_key_check`. See finding 1. |
| Whole-page style visible in review | failed | A boxless style summary was absent from the review JSON despite being present in the canonical and relational exports. See finding 2. |
| Live quantitative verification | not_run | Controlled replies exercise evidence and export handling. They do not establish how well the model reads writing style, language or text. |

The focused suite passed 5 tests. The complete suite passed 300 tests with `PYTHONPATH=src python -m pytest -q` in the locked application environment.

## Findings

1. **Corrected standalone SQLite exports have a dangling lineage foreign key.** `exports._sqlite_bytes` creates a database whose schema expects an in-database `document_versions.parent_version`. `store.copy_version` copies a single snapshot and keeps its original parent number. The fixture's primary database contained `(1, NULL)` and `(2, 1)` and passed the check. The standalone v2 database contained only `(2, 1)` and returned `('document_versions', 1, 'document_versions', 0)` from `PRAGMA foreign_key_check`. Define how standalone exports represent external lineage or include the ancestors needed to satisfy the constraint. Keep the corrected snapshot bytes and lineage number intact.
2. **Review omits the whole-page writing-style summary.** With distinct print and handwriting hints on one raster page, the canonical document and relational exports include the `p1-style` region with `writing: mixed` and no box. The date's review item retains its French language and original image reference, but the bundle exposes neither the page summary nor any writing style for page-only evidence. Add the boxless summary to page references as a model hint. Keep it outside spatial evidence links.

Reproduce both findings from the reviewed SHA with the focused tests. Use a new temporary directory to retain the generated bundles.

```sh
qa_metadata_dir=$(mktemp -d)
PYTHONPATH=src python -m pytest -q tests/test_qa_evidence_metadata.py --basetemp "$qa_metadata_dir"
sqlite3 "$qa_metadata_dir"/test_fresh_store_exports_evide0/out/*/v0002/document.sqlite3 'PRAGMA foreign_key_check;'
rg -n 'page_style|mixed' "$qa_metadata_dir"/test_visual_style_is_page_meta0/out/*/v0001/document.json
rg -n 'page_style|writing' "$qa_metadata_dir"/test_visual_style_is_page_meta0/out/*/v0001/review/review.json
```
