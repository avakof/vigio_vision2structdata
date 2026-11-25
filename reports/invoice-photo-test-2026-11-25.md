# Invoice photo trial · 25 November 2026

The local batch processed all 11 JPEGs in **15 min 23 s**. One photo was a byte-for-byte repeat. The 10 distinct photos produced 10 document exports with **23 line-item rows across 8 documents**. All 10 documents require human review.

## Extraction coverage

Each field count is the number of distinct documents with a non-null invoice-header value that the extractor marked readable and observed. Each document counts at most once per field.

| Header field | Documents with a readable value (out of 10) |
| --- | ---: |
| Document number | 10 |
| Supplier name | 9 |
| Currency | 7 |
| Subtotal | 7 |
| Total | 7 |
| Issue date | 6 |

The invoice schema covers several kinds of commercial document. The model identified 2 as invoices and 3 as delivery notes. The subtype remained unresolved on 5. Two documents yielded no line-item rows. This trial has no independently labeled reference fields or row counts, so transcription accuracy and the number of missed rows remain unmeasured.

## Review findings

The software recorded 96 review issues across the 10 exports. Every document had at least one ambiguous reading. Five photos had a possible cut-off, and three documents had an arithmetic discrepancy involving a subtotal, total or tax amount. Four reread disagreements were retained. No document received human approval.

All 11 batch entries finished with the CLI status `completed`. This records that the software processed each supplied image. The possible cut-offs still require someone to check the original capture.

## Run and export checks

The batch ran on an Apple M3 Max with 48 GiB unified memory using the direct route, the invoice schema and the pinned 4-bit Qwen3.5-9B MLX model. Pipeline version 0.1.3 used a 2,000,000-pixel render budget, up to 4,096 output tokens per call, one retry and one optional crop. A process-scoped macOS network restriction covered inference. Its probe confirmed that loopback worked and the OS denied the external connection.

The 923.28 s wall time covers the CLI batch and its initial exports for all 11 inputs. The separate CSV consolidation and integrity checks ran afterward. The 10 exported versions record 18 model calls. The largest reported model-process resident set was 6.98 GiB.

The project exported JSON, relational CSVs, review bundles and standalone SQLite snapshots for each distinct document. All 10 passed schema validation, file-digest verification, SQLite integrity and foreign-key checks. Each snapshot matched its canonical JSON, and every per-document CSV matched the table rendered from that version. A local archive contains 14 combined CSVs, including the photo-to-document index. The photos, invoice values, tables and review evidence stay local.

[CLI and export instructions](../docs/CLI.md) · [Release evaluation on labeled fixtures](../docs/EVALUATION.md)
