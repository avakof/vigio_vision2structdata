# Measured extraction performance

Both routes ran on the frozen release code and public source bytes under a process-scoped macOS network restriction. The three development sources informed the defaults. The four primary held-out sources and three additional handwriting diagnostics were sealed before candidate prediction. Synthetic cases have separate scores. References are sparse. Each score applies to the labeled facts and relationships in its denominator. [Settings decision](development/evaluation-settings.md) · [Development sources](../fixtures/public/SOURCES.md) · [Held-out sources](../fixtures/archive-heldout/SOURCES.md) · [Handwriting diagnostics](../fixtures/handwriting-diagnostics/SOURCES.md) · [Synthetic cases](../fixtures/README.md)

## Extraction, coverage and review

| Cohort and route | Cases | Documents emitted | Exact fields | Answerable fields covered | Correct when answered | Documents in review | False accepts / auto-accepted cases |
|---|---|---|---|---|---|---|---|
| Archive development · direct | 3 | 3 | 1/8 | 1/7 | 1/1 | 3/3 | — |
| Archive development · hybrid | 3 | 3 | 3/8 | 2/7 | 2/2 | 3/3 | — |
| Archive held-out · direct | 4 | 4 | 12/22 | 12/20 | 11/12 | 4/4 | — |
| Archive held-out · hybrid | 4 | 4 | 11/22 | 12/20 | 11/12 | 4/4 | — |
| Handwriting diagnostic · direct | 3 | 3 | 4/18 | 3/14 | 3/3 | 3/3 | — |
| Handwriting diagnostic · hybrid | 3 | 3 | 6/18 | 5/14 | 4/5 | 3/3 | — |
| Synthetic · direct development | 9 | 5 | 174/227 | 185/223 | 170/185 | 5/5 | — |
| Synthetic · direct held out | 6 | 5 | 68/119 | 81/114 | 67/81 | 5/5 | — |
| Synthetic · hybrid development | 9 | 5 | 217/227 | 214/223 | 213/214 | 5/5 | — |
| Synthetic · hybrid held out | 6 | 5 | 68/119 | 76/114 | 65/76 | 5/5 | — |

A dash marks a metric with zero denominators or no labels in that cohort. Automatic acceptance was disabled throughout. Answerable coverage counts a readable value on the reference row. A wrong or unmatched row can therefore reduce both exact match and coverage. Missing yield is scored only when processed recipe evidence corroborates absence. Document counts can differ from case counts for files rejected during ingestion or PDFs split into separate scopes.

## Business-field checks

| Cohort and route | Cases | Documents emitted | Critical numeric | Row alignment | Parent relationships | Menu price | Recipe quantity, unit or yield | Recipe time or temperature |
|---|---|---|---|---|---|---|---|---|
| Archive development · direct | 3 | 3 | 1/1 | 0/2 | — | 0/1 | 0/3 | — |
| Archive development · hybrid | 3 | 3 | 1/1 | 0/2 | — | 0/1 | 1/3 | — |
| Archive held-out · direct | 4 | 4 | 1/1 | 4/8 | — | 4/4 | 1/6 | — |
| Archive held-out · hybrid | 4 | 4 | 1/1 | 4/8 | — | 4/4 | 0/6 | — |
| Handwriting diagnostic · direct | 3 | 3 | — | 0/5 | — | — | 1/9 | — |
| Handwriting diagnostic · hybrid | 3 | 3 | — | 1/5 | — | — | 3/9 | — |
| Synthetic · direct development | 9 | 5 | 115/158 | 36/50 | — | — | — | — |
| Synthetic · direct held out | 6 | 5 | 7/8 | 10/32 | 15/17 | 11/11 | 4/24 | 1/4 |
| Synthetic · hybrid development | 9 | 5 | 150/158 | 49/50 | — | — | — | — |
| Synthetic · hybrid held out | 6 | 5 | 8/8 | 9/32 | 9/17 | 6/11 | 11/24 | 1/4 |

The archive menu references include item and price pairs. Explicit parent-edge labels for variants, choices and supplements are in the synthetic cohort. A failed row check can reflect an unmatched anchor, a wrong parent or a wrong labeled field.

## Primary archive results by source

| Source | Split | Strata | Direct status | Direct exact | Direct covered | Hybrid status | Hybrid exact | Hybrid covered |
|---|---|---|---|---|---|---|---|---|
| babani-1921 | development | fr, invoice, print, handwriting | incomplete | 1/1 | 1/1 | completed | 1/1 | 1/1 |
| le-versailles-1920-p1 | development | fr, menu, historical, multiple-columns, handwriting | incomplete | 0/3 | 0/3 | incomplete | 1/3 | 1/3 |
| jefferson-ice-cream | development | en, recipe, handwriting, fraction | incomplete | 0/4 | 0/3 | completed | 1/4 | 0/3 |
| carter | heldout | en, invoice, receipt, handwriting | completed | 1/2 | 1/2 | completed | 1/2 | 1/2 |
| delmonicos | heldout | en, menu, print | incomplete | 8/8 | 8/8 | incomplete | 8/8 | 8/8 |
| bedingfield | heldout | en, recipe, handwriting, imprecise, fraction, known-crop | completed | 2/6 | 3/5 | completed | 2/6 | 3/5 |
| lutz-messmer | heldout | fr, recipe, handwriting, imprecise, known-crop | completed | 1/6 | 0/5 | incomplete | 0/6 | 0/5 |

## Language, writing and document family

Strata overlap. A page with printed headings and handwritten entries appears under both styles. Counts and scores refer to whole cases in each stratum. They do not establish accuracy for individual handwritten glyphs or a language population.

| Primary cohort | Stratum | Cases | Direct exact | Hybrid exact | Direct covered | Hybrid covered |
|---|---|---|---|---|---|---|
| Archive development | en | 1 | 0/4 | 1/4 | 0/3 | 0/3 |
| Archive development | fr | 2 | 1/4 | 2/4 | 1/4 | 2/4 |
| Archive development | handwriting | 3 | 1/8 | 3/8 | 1/7 | 2/7 |
| Archive development | invoice | 1 | 1/1 | 1/1 | 1/1 | 1/1 |
| Archive development | menu | 1 | 0/3 | 1/3 | 0/3 | 1/3 |
| Archive development | print | 1 | 1/1 | 1/1 | 1/1 | 1/1 |
| Archive development | recipe | 1 | 0/4 | 1/4 | 0/3 | 0/3 |
| Archive held-out | en | 3 | 11/16 | 11/16 | 12/15 | 12/15 |
| Archive held-out | fr | 1 | 1/6 | 0/6 | 0/5 | 0/5 |
| Archive held-out | handwriting | 3 | 4/14 | 3/14 | 4/12 | 4/12 |
| Archive held-out | invoice | 1 | 1/2 | 1/2 | 1/2 | 1/2 |
| Archive held-out | menu | 1 | 8/8 | 8/8 | 8/8 | 8/8 |
| Archive held-out | print | 1 | 8/8 | 8/8 | 8/8 | 8/8 |
| Archive held-out | recipe | 2 | 3/12 | 2/12 | 3/10 | 3/10 |

## Runtime

Each case uses a fresh worker process per model call. Total wall time includes ingestion, local model loads, retries, validation and report writing. The process RSS column is the largest observed model child’s high-water mark. Parent RSS and MLX peak allocations are recorded separately. The OS filesystem cache was uncontrolled. The Mac entered clamshell sleep at 20:00:11 CET and fully woke at 21:20:05 CET during the direct synthetic cohort. Power changed from AC to battery. The direct synthetic receipt records its measured monotonic duration and UTC timestamps. UTC timestamps span 5,926.65 seconds. The monotonic duration is 1,210.99 seconds. Neither synthetic route recorded a local runtime timeout. Both recorded an invalid reply in the deliberate 32-token truncation case. Use the uninterrupted primary cohorts for latency comparisons. The per-case records contain each page, crop, cap and timing.

| Cohort and route | Cases | Total s | Case s in manifest order | Qwen calls | Extra calls | Crop calls | Qwen tokens | Qwen cap hits | Parser calls | Measured parser crops | Parser tokens | Peak child RSS GiB |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Archive development · direct | 3 | 494.9 | 53.1/280.1/161.2 | 8 | 5 | 2 | 17564 | 3 | 0 | 0 | 0 | 6.98 |
| Archive development · hybrid | 3 | 555.8 | 180.3/209.3/164.6 | 9 | 6 | 3 | 9945 | 1 | 3 | 16 | 1195 | 13.06 |
| Archive held-out · direct | 4 | 193.8 | 32.2/111.4/23.6/21.6 | 7 | 3 | 1 | 6229 | 0 | 0 | 0 | 0 | 6.89 |
| Archive held-out · hybrid | 4 | 471.3 | 109.2/151.5/77.1/131.6 | 8 | 4 | 1 | 6051 | 0 | 4 | 23 | 596 | 10.79 |
| Handwriting diagnostic · direct | 3 | 82.7 | 12.6/53.9/12.2 | 4 | 1 | 0 | 2711 | 0 | 0 | 0 | 0 | 6.95 |
| Handwriting diagnostic · hybrid | 3 | 346.8 | 146.9/117.3/81.3 | 7 | 4 | 2 | 3452 | 0 | 3 | 5 | 535 | 12.51 |
| Synthetic · direct | 15 | 1211.0 | see report | 26 | 14 | 5 | 50386 | 9 | 0 | 0 | 0 | 6.96 |
| Synthetic · hybrid | 15 | 1724.4 | see report | 26 | 14 | 7 | 33066 | 1 | 12 | 107 | 2553 | 12.41 |

The separate cold/resident feasibility tests use one synthetic input per component. They include native parser regions and source coordinates. [Feasibility results](HANDOFF.md#runtime-and-scope)

## Source reports

Archive development measurements include per-case failures and runtime telemetry. [Direct report](../evidence/evaluation/release/direct-development/evaluation.json) · [Hybrid report](../evidence/evaluation/release/hybrid-development/evaluation.json)

Archive held-out measurements include per-case failures and runtime telemetry. [Direct report](../evidence/evaluation/release/direct-heldout/evaluation.json) · [Hybrid report](../evidence/evaluation/release/hybrid-heldout/evaluation.json)

Handwriting diagnostic measurements include per-case failures and runtime telemetry. [Direct report](../evidence/evaluation/handwriting-diagnostics/direct/evaluation.json) · [Hybrid report](../evidence/evaluation/handwriting-diagnostics/hybrid/evaluation.json)

Synthetic measurements include per-case failures and runtime telemetry. [Direct report](../evidence/evaluation/release/direct-synthetic/evaluation.json) · [Hybrid report](../evidence/evaluation/release/hybrid-synthetic/evaluation.json)
