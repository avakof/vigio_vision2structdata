# Independent fixtures and scorer

Status of this dispatch

| Check | Status | Evidence |
| --- | --- | --- |
| Synthetic manifest and label integrity | passed | 15 scenarios over 12 source files. All source and label SHA-256 digests verified. |
| Deterministic regeneration on this host | passed | `python fixtures/generate.py --output .vt-state/qa-rebuilt` followed by `diff -rq fixtures/synthetic .vt-state/qa-rebuilt` returned zero. |
| PDF and image checks | passed | Rendered all pages of the five readable PDFs using PDFium 4.30.0 and inspected the four image variants. PDFium extracted 754 text characters from the French invoice's native first page and zero from its visibly rendered second page. The protected PDF requires a password. |
| Scorer regression tests | passed | `PYTHONPATH=src .venv-qa/bin/python -m pytest -q tests/test_qa_evaluate.py` passed 12 tests with Python 3.12.16 and pytest 8.4.2. |
| Public manifest load | passed | All three cases in the initiating session's `fixtures/public/manifest.json` at `badc616` loaded and passed digest and schema verification. Sparse ingredient and dish labels should declare `"anchor": "name"` to avoid using the first row within a kind as their alignment. |
| Draft contract wiring | passed | Reran all 12 tests against a local snapshot of the `badc616` package and the main worktree's locked environment. A fake extractor returning a real typed `Document` exercised `evaluate.run`, canonical projection, exact scoring and snapshot output. |
| Canonical evaluation against integrated pipeline | not_run | The adapter targets the documented `pipeline.extract` call and projects canonical documents plus job artifacts. Verify field, issue and error vocabulary when the specialist code lands. |
| Direct and hybrid extraction accuracy and model timing | not_run | This dispatch produced labels and scorer tests. Actual extraction requires the integrated runtime. The hybrid provisioning blocker is tracked separately. |
| Offline and storage acceptance | not_run | Still need production-path checks for process-scoped offline inference, reimports, configuration changes, crash recovery, corrections and regenerated JSON, CSV and SQLite version consistency. |

The scorer tests exercise wrong parent edges, wrong order, unexpected page scope, guessed unresolved values, false acceptance, all-abstained output, sparse labels, source-wide incomplete coverage, digest validation and separate real and synthetic aggregation. Fixture integrity checks also cover actual PDF text-layer differences, legible last-page rendering, duplicate rows, encrypted and corrupt bytes, and raster rotation/crop. The inert credit note contains a harmless arithmetic expression as a row name, an instruction to change the extracted total and a reserved-domain URL. No tools or network requests are driven by those strings.

These are the synthetic label denominators before extraction. Bounded-failure cases can contribute status, issue and coverage checks without field labels. The fixtures with related source content share a split. The synthetic development and held-out splits have zero genuine handwriting samples.

| Split | Cases | Fields | Answerable | Unresolved | Critical numeric | Menu price | Recipe quantity/unit/yield | Rows | Parent edges | Ineligible cases |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Development | 9 | 227 | 223 | 4 | 158 | 0 | 0 | 50 | 0 | 8 |
| Held out | 6 | 117 | 112 | 5 | 8 | 11 | 26 | 32 | 17 | 4 |

Regeneration used Pillow 11.3.0 and ReportLab 4.4.3 with these macOS supplemental fonts.

| Font | SHA-256 |
| --- | --- |
| Arial.ttf | `525979822591a3447cfc49d943d6f7683508e25543407871c0ed8fed05fd2bd9` |
| Arial Bold.ttf | `d72db21f9242aedd6b917d8549ad5921766b24d5f8d0becfda2ff4c620b3c2e0` |
| Times New Roman Bold.ttf | `50f5b1f27b7d34621090e437e8b1dd4510a22d2823fc876b3286bed327b64bbb` |
| Bradley Hand Bold.ttf | `b94c10ed01e88fd8dbbee95485d3863a0719520e8637ae5a9a30cf485e18740d` |
