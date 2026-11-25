# Local extraction and evidence

The core package runs on CPython 3.12. SQLite, model files, runtime environments, copied inputs and intermediate artifacts live under the selected state directory. The core, MLX and Paddle Python dependency sets have separate locks. Explicit provisioning fetches immutable model revisions and checks every file against its pinned digest. Application processing uses local files.

## Identities and grouping

A job records one attempt. Its artifact directory is allocated exclusively. A source is identified by SHA-256 of its exact bytes. A document ID hashes the source and its intended ordered page numbers. Each extraction configuration has a fingerprint combining model and component revisions, runtime, route, schema, pipeline revision, page grouping, and processing budgets. A correction appends a version to that document. The version and originating job resolve all artifact references.

Singleton pages are the conservative default. An explicit group joins continuation pages. Ungrouped processed pages remain independent documents. The source inventory records all known skipped pages. With a budget that admits two pages of a three-page group, the group still identifies pages 1–3 and the result remains incomplete. The original photograph's capture extent starts as `not_established`.

## Stages

The CLI starts a job; ingestion preserves bytes, dimensions, page order, text-layer geometry and rendered images. Page readings retain model attempts and report overflow, skips and failures. The adapter alone assigns geometry and runtime metadata. A model proposes document data through a bounded wire schema. The domain layer checks vocabulary, normalizes field readings, checks relationships and computes derived observations with input references. The pipeline checks canonical types and the generated JSON Schema before persistence and export.

Coordinates use original top-left pixel edges for an image and original top-left page points for a PDF. The render-to-original affine uses `x = a*u + c*v + e` and `y = b*u + d*v + f`. Grounded boxes store their precision. Page and crop references can support a reading where its exact glyph position is unknown.

## Review and replay

Raw text, a normalized value, reading state, source references, origin and alternatives belong to each observation. A calculated amount cites its operands. A printed amount remains an observed field even when arithmetic disagrees. Occurrence IDs preserve identical printed line items. Parent IDs express containment such as an item’s variants, a bundle’s choices and a recipe’s ordered steps.

No automatic acceptance policy has been calibrated. The default decision is `needs_review`. Approval requires an attributable event on the applicable base version, sufficient coverage and resolved review issues. Informational notes stay visible. Store history preserves the original extraction, the corrections payload, the effective values and their evidence. Exports select one immutable version. CSV applies spreadsheet-cell escaping at the export boundary; JSON keeps the actual text.

The initial audit, frozen API and tested acceptance evidence are in [the audit](development/contract-audit.md), [interface specification](development/interfaces.md), [freeze decisions](development/freeze.md), and [release handoff](HANDOFF.md).
