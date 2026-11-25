# Evaluation fixtures

`manifest.json` describes 15 scenarios over 12 original fictional source files. Source and label digests are verified before any extraction starts. The source generator is independent of the labels, prompts, normalization and inference code. Label files are manual transcriptions and expectations for the visible documents and processing scenarios.

| Family | Split | Scenarios |
| --- | --- | --- |
| Aulnes invoice and Harbor credit note | Development | French invoice with 24 rows, repeated legitimate lines, explicit continuation, native and raster pages, a final-page item and evidenced packaging. English credit note with negative amounts, a currency symbol with no explicit currency, a date with two readings, mismatched total, a formula-shaped description and an inert instruction. Also unrelated page grouping, encryption and source bounds. |
| Malformed and unsupported media | Development | Corrupt PDF, corrupt PNG, unsupported text file. |
| Paperwing invoice | Held out | Printed English invoice with decimal dots, explicit USD and unambiguous date. |
| Érables menu | Held out | Two columns, bilingual labels, dotted leaders, variants, a supplement and a bundle with two sets of choices. A blurred JPEG crops away prices at the right edge. |
| Scones recipe | Held out | Simulated handwriting with a fraction, range, missing yield, unreadable digit, strike-through and replacement, optional ingredient, substitution and “au goût.” A JPEG rotates the same document. A short-token run exercises visible truncation. |

Related scenarios and source bytes stay in one split. The two split labels are relative to intended use. The assets are visible in the repository, so a score on a family used to tune the extractor is a development result. Synthetic handwriting is made from a handwriting-style font and deliberate visual marks. Genuine handwriting accuracy requires a separate public or authorized real-document manifest.

## Reproduce the source files

The committed bytes are the evaluation inputs. To reproduce them on the reference macOS host, use Python 3.12, Pillow 11.3.0 and ReportLab 4.4.3. Fonts are read from the macOS installation. System font files are absent from this repository.

```sh
python fixtures/generate.py --output .vt-state/qa-rebuilt
diff -r fixtures/synthetic .vt-state/qa-rebuilt
```

Font SHA-256 values from the generation host are in the [creation record](../evidence/development/qa-fixtures.md). System fonts and compression libraries can produce different hashes on other hosts. If an intentional source or truth change is reviewed, update its manifest hashes together with the change.

## Run and interpret

```sh
vision-tables evaluate fixtures/manifest.json --output .vt-state/evaluation/synthetic
```

Use a separate manifest for vetted public data, setting provenance origin to `real`, recording source and license and hand-checking each labeled field. A combined manifest is also supported. Its aggregates remain keyed by origin, split and stratum. Strata overlap and their denominators cannot be added. Case settings may override bounded inspection or reading budgets, document type or page grouping. Model choice, route and runtime state come from the invocation.

- Fields ordinarily match by page scope, entity kind and ordinal within kind. This preserves repeated rows and tests order. For a sparse real label, set an entity's `anchor` to a labeled text field name, such as `name`, to match a unique readable row anywhere within its kind. A field can require value, state, alternatives, raw text, source page, origin or derivation inputs. Text is exact and decimal labels compare finite numeric values.
- `field_exact` uses all labeled fields, including uncertainty and absence. `answerable_field_coverage` divides readable, non-null answers by fields with a recoverable value. `accuracy_when_answered` uses only those answered fields. Zero coverage makes conditional accuracy undefined. `unresolved_guess_rate` counts non-null answers against intentionally unresolved labels.
- `row_alignment` uses labeled rows and checks all their labeled fields and any declared parent. `entity_count` checks for missing and extra rows of exhaustive kinds. `relationship` uses declared parent edges. `critical_numeric`, `menu_price` and `recipe_quantity_unit_yield` use their respective labeled field subsets. Extra fields outside labeled or exhaustive checks have unknown accuracy.
- Review and automatic acceptance rates use emitted documents with known approval state. False acceptance is the share of auto-accepted source cases with any labeled mismatch or an explicit ineligibility label. Unsafe acceptance on ineligible cases uses all such cases as its denominator. Human approval is separate. At launch the expected automatic acceptance denominator is zero, leaving its error rate undefined.
- Every runtime sample includes observed end-to-end seconds, processed-page count, process peak RSS and available backend metadata. The per-processed-page summary divides elapsed time for samples that produced pages by the number of such pages. Noncompleted runs are counted separately. The default phase is unspecified. A measurement driver must verify and declare a cold or warm phase explicitly. Child runtime memory depends on backend telemetry.
- `evaluation.json` lists mismatches by location. The local case directories retain successful canonical result snapshots, and job IDs reference stage artifacts under the configured state directory.
