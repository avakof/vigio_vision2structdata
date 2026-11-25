# Domain package (`vision_tables.domain`)

Extraction semantics: vocabulary, normalization, evidence graph, arithmetic, review policy and corrections.
It never calls a model, reads files or touches SQLite. Every function is deterministic, so a stored
`CorrectionSet` replays to the same version.

## Entry points

| Function | Contract |
|---|---|
| `assemble(ingested, readings, scope, run, document_id) -> Document` | Inputs outside `scope` are ignored. Builds entities (global source-order `ordinal`), observations, coverage, issues. Raises `ProcessingError` only for an invalid scope or two readings of one page; everything else is a visible issue. |
| `revalidate(document) -> Document` | Same version. Recomputes calculated observations and domain issues, keeps ingestion/adapter/assembly issues. `revalidate(assemble(x)) == assemble(x)`. An approval survives only while the review gate is clear. |
| `apply_corrections(document, changes) -> Document` | Version N+1 with `parent_version=N`. Atomic: any invalid change applies none. `ProcessingError` codes: `document_mismatch`, `stale_base`, `invalid_correction`, `approval_blocked`. The input is never modified. |
| `review_gate(document) -> dict` | `approvable`, `blockers` (code, message, resolution), `reasons`, `informational`. |
| `vocabulary()`, `model_vocabulary(doc_type)`, `FIELD_TYPES` | Machine-readable vocabulary. `python -m vision_tables.domain.schema` prints it. |
| `CATALOG`, `review_gates()` | Issue codes, severity, per-type blocking codes and how each is resolved. |
| `examples.EXAMPLES`, `examples.example_context()` | One small `PageReading` per type (`invoice_page`, `menu_page`, `recipe_page`) and a synthetic `Ingested` + `Run`. |

Adapters should import `model_vocabulary("invoice" | "menu" | "recipe")` for prompts and grammars: per
kind, the allowed parent kinds and the observable fields with their lexical type, about 2 KB per family.
Field names are exactly those of the coordinator-owned `vocabulary.py`; an import-time assertion fails if
they drift. `schema.py` adds lexical types, critical fields and containment on top.

## What a reading must contain

Raw text exactly as seen, a short supporting quote, `alternatives` only for real uncertainty, a parent link
to the page-local container (omitted on continuation pages), and no calculated fields, geometry or
confidence. `unsupported` kinds/fields are dropped with a `review` issue (`unsupported_kind`,
`unsupported_field`): they signal an adapter that skipped `ModelPayload` validation.

## Normalization (raw is never altered)

| Type | Rule |
|---|---|
| money / number | `1 234,50`, `1,234.50`, `1.234,50`, `1'234.50`, NBSP/thin-space grouping give `1234.50`. A single separator followed by exactly three digits (`1,234`) is ambiguous and keeps both readings. `0,335` is decimal. Sign as printed: `(12,50)`, `12,50-`, `-12,50`. Currency marker and `HT/TTC` words are stripped from the amount only. |
| fractions | `⅓` becomes exact `1/3` (reduced `n/d` only when the decimal does not terminate), `1½` becomes `1.5`. |
| quantity | A range keeps `value: null` and the bounds appear as calculated `quantity_min/max`; imprecise phrases (`to taste`, `au goût`, `une pincée`, `q.s.`) keep `value: null` with a reason. A printed unit (`250 g`) becomes a calculated unit when the unit field is absent, and `unit_conflict` when it disagrees. |
| date | ISO. `03/04/2026` has `value: null`, `state: ambiguous`, alternatives in both orders, unless another numeric date of the same document forces the order (reason recorded). Two-digit years stay ambiguous. French and English month names, ordinals and weekdays are read. |
| currency | `€`/`EUR` give `EUR`. `$` gives null with `USD, CAD, AUD`; `¥` gives `JPY, CNY`. Never inferred from language. |
| unit | FR/EN aliases to canonical codes. Mass and volume convert exactly within a dimension. Spoon, cup, pinch and bunch are `culinary`: no factor, ever; volume is never converted to mass; `fl oz`, pint and gallon are deliberately absent. |
| percent | percentage points `0..100` (`5,5 %` gives `5.5`). |
| temperature | The scale must be printed and match the field: `180°` in `temperature_c` is ambiguous with `180 C` / `180 F`. |
| flags, enums | strict booleans and closed enums with French/English words (`Facture d'avoir` gives `credit_note`). |

Ambiguous means: raw preserved, `value` null, candidates in `alternatives`, a `reason`. A readable
observation with a null value is only a range or an imprecise quantity, explained in `reason`.

## Calculations (`calc.v1`)

All values are exact rationals. Money is rounded half away from zero to the currency minor unit (2 unless
the currency says otherwise) once per calculated line; totals sum printed line totals. Calculated
observations carry `origin=calculated`, a `derivation.calculation` string starting with the version, and
input observation ids. They are never editable.

* `pack_total_quantity = quantity x pack_count x pack_unit_quantity` in `pack_unit`; only when all four are
  resolved. Two cartons of 6 bottles of 1 L give `12` (`l`), inputs: the four observations.
* `calculated_line_total = round(unit_price x multiplier - discount)`, tax excluded. The multiplier is the
  quantity when the price basis is absent or equal to the purchase unit, a same-dimension conversion, or the
  derived pack total when the basis is a mass or volume unit. Otherwise `line_check_indeterminate`.
* `calculated_total = (subtotal | sum of line totals) + tax + charges - discounts`, absent components omitted
  and named in the derivation. Each component prefers the printed aggregate and falls back to the child
  rows. Mismatches never change a printed amount.

## Issues, severity and approval

`Issue.severity` is `review` (unresolved, blocks approval) or `info` (retained note). Each `review` code is
resolvable by correcting an existing observation except those marked `reprocess` in `CATALOG`. Type-specific
gates (`review_gates()`):

* **invoice**: unresolved critical reading (`total, issue_date, currency`, line `description, quantity,
  unit_price, line_total`, tax `rate, amount`), `line_total_mismatch`, `subtotal_mismatch`, `total_mismatch`,
  `tax_*_mismatch`, `charge_sum_mismatch`, `discount_sum_mismatch`, `pack_purchase_unit_conflict`,
  `due_before_issue`, `currency_conflict`. Info: `total_consistent_without_tax` (tax-inclusive pricing is
  not inferred), `rounding_policy_difference`, `line_check_indeterminate`, `total_check_indeterminate`.
* **menu**: `detached_price` (a price without a readable name), `invalid_item_ref`, `translation_same_language`
  (pairing unresolved), `replacement_without_crossout`. Info: `item_without_price`, `price_and_variants`,
  `translation_without_layout_evidence`, `crossed_out_reading`. Dietary/allergen claims without a supporting
  quote become ambiguous, never stored as claims.
* **recipe**: unresolved `name`/`quantity`/`instruction`, `yield_type_conflict`, `range_conflict`,
  `unit_conflict`. Info: `recipe_yield_missing` (null stays null; no per-portion figure exists in this schema).
* **all**: `ambiguous_reading`, `unreadable_field`, evidence problems (`ungrounded_reading`,
  `evidence_mismatch`, `evidence_not_in_region`, `invalid_evidence`: `review` for critical fields, `info`
  otherwise), `suspected_cutoff`, incomplete coverage, empty documents, wrong-type pages.

Approval (`CorrectionSet.approve`) is accepted only for the exact resulting version when the gate has no
`review` issue, coverage is complete, no cutoff is suspected, the type is known and observations exist.
Information notes are retained. An unresolved non-critical reading stated by a human is `info`.

## Corrections

A change targets an existing, non-calculated observation and must carry a canonical typed value (decimal
string, ISO date, ISO currency, canonical unit code, closed enum, boolean, `n/d` fraction, id of a
`menu_item`) or a non-readable state with a reason. The replacement keeps the observation id, becomes
`human_supplied`, keeps the model's evidence, appends a `human` evidence entry and records the model's
original reading in `reason`. The previous version retains the untouched original. Dependent calculations,
issues and approval are recomputed; a later edit resets approval.

## Limitations (known, intended)

* No way to add or remove a record or re-parent one: corrections edit observations only.
* A supplier's own arithmetic error cannot be waived: `*_mismatch` blocks approval until an input
  reading is corrected. A waiver needs a contract field.
* `suspected_cutoff`, incomplete coverage and `source_skipped_pages` make a version unapprovable (contract
  guard); the remedy is re-capture or reprocessing.
* A model quote is checked only against the raw reading and the cited region text; it cannot prove the
  quote exists in the image.
* Decimal-versus-grouping for `1,234` and two-digit years are never guessed; they cost a review.
* The units table covers common FR/EN kitchen and trade units, not every regional spelling; unknown units
  are ambiguous, never guessed.
