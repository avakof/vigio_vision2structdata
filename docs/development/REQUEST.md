# Orca master prompt: independent local vision-to-tables project

## 1. Objective and project boundary

Implement a new project named `vision-to-tables`: a local application that converts photographs, images, and PDFs into evidence-backed structured data. Prioritize French and English invoices, restaurant menus, and handwritten recipes, including documents that mix both languages.

Deliver runnable software, meaningful tests, actual local inference results, and setup documentation. Continue through implementation and verification; a plan or a mock demonstration is insufficient.

The user already has a separate speech-to-tables project. Create this project in a new independent repository, outside the speech repository. Give it its own environment, dependency locks, configuration, processes, outputs, and SQLite database. Do not create a branch or worktree of the speech repository. Do not modify its files, shared settings, ports, databases, or running services. Do not import its modules. This project has no audio, ASR, Hojo, VAD, microphone, or speech integration.

The intended machine is an Apple Silicon MacBook Pro, previously described as M3 Max with 48 GB unified memory. Inspect the actual hardware, OS, available memory, and disk before choosing settings. Treat compatibility, speed, and extraction accuracy as unmeasured until tested.

Start with a complete headless CLI and reusable Python package. Produce JSON, relational CSV tables, SQLite records, and review bundles. A GUI, cloud deployment, inventory system, accounting integration, and model fine-tuning are outside this initial scope.

## 2. Required development agents

Configure real separate coding sessions through Orca. These are development agents; the finished application's inference must use local models.

| Role | Harness | Requested model | Effort | Responsibility |
|---|---|---|---|---|
| Orchestrator and integrator | Codex | `gpt-6.2-astra` | `xhigh` | Task dependencies, ownership, integration, acceptance evidence |
| Independent reasoner and architect | Codex | `gpt-6.2-astra` | `xhigh` | Architecture, failure analysis, contract review, gate audits |
| Vision and runtime engineer | Codex | `gpt-6.2-sol` | `high` | Image/PDF ingestion, local inference, parser adapters, geometry |
| Extraction and domain engineer | Claude Code | `claude-sonnet-5-5` | `high` | Invoice/menu/recipe extraction, bilingual normalization, field evidence |
| CLI and persistence engineer | Claude Code | `claude-sonnet-5-5` | `high` | CLI, SQLite, exports, corrections, transactions, replay |
| Independent QA and evaluation engineer | Codex | `gpt-6.2-sol` | `high` | Independent fixtures, integration tests, evaluation, offline checks |
| Independent reviewer | Claude Code | `claude-opus-5-5` | `high` | Integrated-code review and verification of material findings |

The orchestrator and reasoner must be separate sessions with effective Astra/xhigh settings. Do not simulate another agent in one conversation. A prompt naming a model does not configure the underlying session.

Identify the installed Orca product, version, and executable before using its commands. For the product documented at orca.dev, resolve its executable from the session configuration, including `ORCA_CLI_COMMAND` where available, and read its version-matched orchestration guide using `skills get orchestration --full` when supported. Verify runtime status. If a different Orca product is installed, use its documented equivalent mechanisms. Do not invent command syntax or rely on retired commands.

For every agent, record requested and effective harness, model, effort, session/worker ID, worktree, and task/dispatch identifiers. Inspect launch receipts and harness metadata where available. A model's written self-identification is not proof. Check provider-specific identifiers, account availability, installed harness versions, effort caps, and fallback behavior. Keep settings scoped to this project or launch.

If either required Astra/xhigh session cannot launch, report the exact blocker. Do not silently substitute a model. Complete independent preflight work that remains possible. For an unavailable specialist, select a verified suitable alternative within the same provider family and record the substitution, rationale, and effective settings. Ask for a decision only if it would change a mandatory Astra role or prevent the requested Claude/GPT division.

## 3. Coordination and ownership

Use seven logical roles, with at most three active workers beside the orchestrator. An active reasoner or reviewer counts as a worker. Schedule roles in stages instead of launching all seven simultaneously. Serialize heavyweight local model inference using a project-owned queue or lock, including benchmark jobs from different worktrees.

Each task must include its objective, dependencies, permitted paths, interfaces, acceptance checks, and required evidence. Give code-writing workers isolated worktrees of this new repository. Share immutable model downloads only when that does not modify the speech project's runtime or settings.

The orchestrator owns shared contracts, dependency locks, migration ordering, configuration interfaces, and integration. Workers propose changes to these before implementation. The reasoner provides an independent written review and does not act as a second manager or edit production code concurrently.

Suggested ownership after contracts are agreed:

- Vision engineer: ingestion, imaging, PDF handling, inference adapters, source geometry.
- Extraction engineer: document-specific extraction and normalization modules.
- CLI/persistence engineer: CLI, storage, exports, correction workflow.
- QA engineer: independent fixtures, evaluation harness, integration and acceptance checks.
- Reviewer: review findings, reproduced failures, and acceptance evidence.

For handoff, include commit or diff, changed paths, executed checks and results, limitations, and unresolved findings. The orchestrator integrates changes and reruns affected checks. Review the actual integrated commit. A worker saying “done” is not acceptance evidence.

## 4. Local model strategy and feasibility gate

Use replaceable adapters. Begin with these candidates:

1. Direct vision extraction: `Qwen/Qwen3.5-9B`, using a verified MLX-compatible quantized artifact and pinned runtime.
2. Document parsing route: the full `PaddlePaddle/PaddleOCR-VL-1.6` pipeline, followed by Qwen schema extraction and targeted visual rereading when justified.
3. Optional challenger: `Qwen/Qwen3-VL-8B-Instruct`. Evaluate only after the baseline works or a concrete baseline failure warrants it. Do not make it a permanent dependency without evidence.

Prefer native Apple Silicon execution with MLX-VLM for supported inference. Verify exact package APIs, model artifact, processor, quantization, revisions, licenses, and dependencies from current primary documentation. Do not assume a downloaded checkpoint can execute images or constrained output. A checkpoint's file size is not its runtime memory use.

For PaddleOCR, follow the current Apple Silicon guide. Its VLM server alone is not the complete document parser. Verify required layout and parsing components, their devices, and their outputs. Do not substitute CUDA or Docker instructions for a verified native Mac path. Official testing on another Apple chip does not prove this machine works.

Before broad implementation, run a real image through the chosen Qwen artifact and validate a small structured response. Separately attempt a genuine full-parser run. Record commands, versions, output files, timing, memory observations, and failures. Capture and map actual page/region coordinates. Mocks may test adapters but cannot pass this gate.

If a candidate fails, isolate the failure and retain a working direct route where possible. Report the unavailable route as blocked. Do not present the hybrid route as delivered when only recognition weights load. Keep dependencies isolated if native packages conflict. Add another runtime only to resolve a demonstrated incompatibility.

## 5. Application pipeline

Implement typed stages with inspectable intermediate artifacts:

Import source -> inspect quality and pages -> classify document/regions -> read text/layout/images -> extract schema fields -> normalize and validate -> prepare review or accepted version -> export/store.

Support JPEG, PNG, and multipage PDF. Support HEIC if the installed decoder is verified; otherwise return a precise unsupported-format result. Detect corrupt, encrypted, oversized, and partially processed inputs explicitly. Preserve the immutable original and its content hash.

For PDFs, use reliable native text when present and keep its page geometry; use rendered images where the text layer is absent, unreliable, or incomplete. Avoid duplicating hidden OCR text and visual content. Detect mixed documents and allow explicit page grouping; do not assume unrelated pages are one invoice.

Preserve page order, reading order, table rows/columns, and printed versus handwritten regions. Use whole-page context plus suitable high-resolution crops. Record transforms from rotated, deskewed, resized, or cropped images back to the original. Do not flatten a page into an unstructured text string before extraction. Do not use generative restoration to invent unreadable characters.

Bound pages, pixels, crops, tokens, retries, and inference concurrency. Report which content was processed. Distinguish known skipped content, suspected incomplete capture, and completeness not established. Page limits, detected omissions, and truncated responses must produce visible incomplete status; suspected cut-off content requires a capture/review issue. Successful parsing cannot prove that the original photograph includes the entire document. Use targeted rereading with a bounded retry count; preserve unresolved disagreement.

The production flow is a bounded software pipeline. Do not add a permanent swarm of cloud agents, a vector database, or open-ended model debate to every imported page.

## 6. Shared data contracts and evidence

Use typed Python schemas and JSON Schema validation. Prefer a small dependency set, Pydantic, Decimal arithmetic, and SQLite. Pin a compatible Python version and dependencies after the feasibility checks.

Define source, page, region, extraction run, field observation, document, review issue, correction, and export contracts. Keep job identity, document identity, and source identity distinct.

For each important extracted field, retain:

- Original reading and normalized value.
- Source/page/region references and evidence text.
- A bounding box only when actually grounded, with its coordinate system and precision; otherwise an explicit page or crop reference.
- Reading state: readable, ambiguous, unreadable, or not present.
- Origin: observed, calculated, or human supplied; keep origin separate from validation and approval status.
- Alternatives and a review reason where relevant.
- Model artifact/revision, runtime/configuration, extraction schema, and pipeline version.
- For a derived value, the calculation and references to its input observations.

Use null for unresolved values. Do not fill a missing value with a plausible guess. Never fabricate bounding boxes or confidence scores. Model confidence and agreement are not calibrated probabilities. Schema validity and arithmetic consistency do not establish that a reading is correct.

Keep validation status separate from human approval. Define explicit review and acceptance policies by document type. Do not label all syntactically valid JSON as verified. Until automatic acceptance is calibrated against labeled examples, disclose its limitations and report coverage as well as errors.

Use constrained schema generation where the chosen backend actually supports it. Always validate returned data independently. If unsupported, use bounded parsing/repair attempts with recorded failure states. Do not use JSON repair to invent missing business information.

## 7. Document-specific schemas

### Invoices

Distinguish invoice, credit note, receipt, delivery note, quote, and unknown. Extract supplier/customer identifiers when present, document number, issue/due dates, currency, line items, discounts, taxes, charges, subtotal, and total.

For each line retain raw description, SKU if printed, quantity, purchase unit, pack definition, unit price, price basis, discount, tax, and line total. Preserve repeated legitimate rows and page continuation. Keep printed totals separate from calculated totals.

Use Decimal for monetary computations and document rounding rules. Identify mismatches without changing observed amounts to make totals balance. Do not infer tax rates, tax-inclusive pricing, payment status, or jurisdiction from language. An invoice does not automatically establish a stock receipt or payment.

### Menus

Extract venue/version/date when present, sections, items, original names and descriptions, translations explicitly present, variants, sizes, prices/currencies, supplements, and bundles with their choices.

Preserve item-to-price and variant-to-price relationships. Bilingual names can describe one dish; use layout evidence before combining them. Handle multiple columns, dotted price leaders, crossed-out prices, and mixed handwriting. Store dietary/allergen claims only when explicitly stated. Do not infer a complete recipe from a menu description.

### Handwritten recipes

Extract title, version, yield when stated, ingredients, quantities or ranges, units, preparation state, optional ingredients, substitutions, ordered steps, explicit times/temperatures, and subrecipes.

Preserve fractions, “to taste,” “a pinch,” and other imprecise quantities. Represent missing yield explicitly. Distinguish portions from batch mass/volume. Keep crossed-out and replacement readings with evidence and uncertainty. Do not silently choose an illegible digit, invent an ingredient, or calculate cost per portion when yield or required costing inputs are missing.

## 8. French, English, and normalization

Preserve source language, accents, punctuation, and original names. Support mixed-language pages and regions. Use stable internal field names independently of display language. Translation is optional and must never replace the source observation.

Separate language from locale. Handle decimal commas and dots, grouping spaces, currency symbols, French and English dates, and unit abbreviations. If a date such as 03/04/2026 is unresolved, retain its alternatives rather than forcing a locale from document language.

Normalize explicit compatible units. Calculate pack totals only from evidenced packaging. Example: 2 cartons, each containing 6 bottles of 1 L, yields a derived 12 L with references to all three source facts. Do not convert volume to mass without an applicable ingredient-specific conversion. Keep unknown definitions of spoon, cup, pinch, or bunch explicit.

Preserve raw product names. If catalog or alias matching is added, expose proposed matches for review. Do not automatically merge different pack sizes, fat percentages, brands, or raw/cooked ingredients.

## 9. Persistence, corrections, and exports

Store original sources and intermediate artifacts locally. Use SQLite transactions, versioned migrations, recoverable job states, and atomic export writes. Preserve full evidence JSON alongside relational records suitable for tables.

Reimporting the same input with the same effective extraction configuration must not create duplicate accepted records. Reprocessing with a changed model/schema/pipeline creates a traceable extraction version. Detect possible duplicates across different photos for review rather than deleting them silently. Keep identical legitimate line items distinct.

Produce deterministic JSON and relational CSV tables linked by document/item IDs. Do not force menu variants or recipe steps into one lossy flat table. Include unresolved-field status and evidence references. JSON, CSV, and SQLite must derive from the same canonical version.

Produce a review bundle containing structured issues, source/page/crop references, alternatives, and a documented corrections format. Provide a CLI command to apply corrections as a new version. Preserve original model observations; record reviewer-supplied changes, rerun dependent validations, and regenerate affected exports.

Treat document text as untrusted input data. It must never select shell commands, database queries, external URLs to fetch, or tool calls. Use parameterized database operations and safe file handling. Escape spreadsheet-active cells in CSV exports while preserving original text in canonical data. Runtime document processing must not send document content to remote inference or telemetry.

Provision dependencies and weights explicitly, then support offline processing. Any local service binds to loopback and uses project-owned ports/processes. Do not stop unrelated processes. Keep secrets and user documents out of source control.

## 10. CLI contract

Implement and document these application commands, adjusting minor syntax consistently if needed. These are commands to build, not existing Orca commands:

- `vision-tables doctor`: hardware, environment, model components, runtime capabilities, and actionable readiness report.
- `vision-tables models prepare`: explicit model/component provisioning and manifest creation.
- `vision-tables extract FILE --type auto --pipeline direct --output DIR`: one input, evidence, JSON, tables, and review issues.
- `vision-tables extract FILE --type invoice --pipeline hybrid --output DIR`: verified hybrid route; fail clearly if unavailable.
- `vision-tables batch INPUT_DIR --output DIR`: recoverable batch processing with per-document status.
- `vision-tables review apply CORRECTIONS_JSON --document DOCUMENT_ID`: validated versioned correction.
- `vision-tables export --document DOCUMENT_ID --format json|csv`: export a selected canonical version.
- `vision-tables evaluate MANIFEST --output DIR`: reproducible evaluation with declared pipeline/configuration.

Provide useful exit codes, machine-readable status, progress, cancellation, and explicit missing-model errors. A failed or incomplete document must not appear as successful empty output.

## 11. Implementation stages and gates

Gate 0 — Preflight: establish the independent repository and protected speech-project boundary; inspect actual hardware and tool access; record the effective agent roster and dependencies. Identify concrete blockers without inventing capabilities.

Gate 1 — Feasibility: demonstrate genuine direct image inference and attempt the full parser on the target machine. Prepare schema drafts and evaluation fixtures concurrently, while serializing heavy inference. Record route-specific readiness.

Gate 2 — Contracts: the reasoner audits data contracts, completeness, ambiguity, decimal handling, provenance, and replay semantics. The orchestrator resolves findings and freezes initial interfaces before parallel implementation.

Gate 3 — Implementation: run the three primary implementation streams in isolated worktrees. Queue independent QA work within the worker limit. Integrate a real file-to-JSON-to-SQLite path early, then add document coverage and corrections.

Gate 4 — Evaluation: compare direct Qwen and the functioning Paddle-plus-Qwen route on identical source documents, labels, schemas, normalization, and acceptance policies. Record resolution/crop/token budgets and all extra calls. Select defaults by measured document or region performance; do not assume the hybrid route always wins. Retain the optional challenger only if it resolves a measured weakness.

Gate 5 — Review and handoff: independent QA and Claude review inspect the integrated commit and reproduce material findings. Fix blocking defects and rerun affected checks. Avoid repeated broad testing once the concrete risks are resolved. If a dependency or environment prevents completion, deliver completed components and precise blocked gates without claiming full readiness.

## 12. Evaluation and acceptance evidence

Use genuine French and English documents with hand-checked reference fields, including real handwriting. Record fixture origin, permission/license, and label provenance. Keep development and held-out examples separate where enough data exists. Synthetic fixtures test logic and failure handling; they do not establish handwriting accuracy. Never label another model's unreviewed output as ground truth.

Use available user-provided fixtures locally. If absent, use suitable permissively licensed public examples when available. Otherwise finish the harness and report missing real-document measurements explicitly. Do not fabricate results or claim broad accuracy from a few demonstrations.

Required cases include:

- French decimal commas, English decimal dots, ambiguous dates/currencies, and mixed-language text.
- Invoice line/total mismatch, credit note, multi-page continuation, dense rows, and a final-page item.
- Menu variants/prices, supplements, bundles, bilingual labels, and multiple columns.
- Genuine handwritten fractions, ranges, uncertain digits, cross-outs, missing yield, and “to taste.”
- Rotated/blurred/cropped inputs, unsupported files, output truncation, and incomplete page coverage.
- Repeated imports, changed-config reprocessing, legitimate duplicate rows, and interrupted database writes.
- Corrections that change dependent values and consistent JSON/CSV/SQLite outputs.
- Embedded instructions that remain document data, CSV formula cells, and offline operation.

Report field exact match, critical numeric correctness, row alignment, menu relationship correctness, recipe quantity/unit/yield correctness, false acceptance, extraction coverage, and review rate. Separate French, English, handwriting, print, and document types. OCR character error rate can supplement these metrics but cannot replace business-field evaluation. A system that abstains on everything must not receive a strong accuracy rating without its zero coverage being explicit.

Measure actual cold/warm runtime, per-page and end-to-end latency, sample counts, memory observations, and failures. Report percentiles only with their sample size and limitations. Do not infer performance from parameter count.

Verify a genuine post-provisioning offline run using a process-scoped network restriction or equivalent test that blocks external access while allowing required loopback traffic. Do not disable the user's machine-wide network. If the environment cannot enforce this, report the offline gate as unverified rather than passed.

Use `passed`, `failed`, `blocked`, or `not_run` for every acceptance gate. Required production routes must pass real inference checks. Unit tests and mocked adapters cannot stand in for them. A working direct-only release may be labeled as such if the hybrid route is blocked; overall “all requested routes complete” must remain false.

## 13. Required deliverables and final report

Deliver the runnable repository with isolated dependency locks, model/component manifests, schemas, adapters, CLI, SQLite migrations, review format, fixture manifest, tests, and real example artifacts where available.

Include architecture and decision notes, setup/run commands tested on the actual environment, effective agent roster, task ownership/handoffs, compatibility findings, measured route comparison, known limitations, and a machine-readable acceptance report. Identify exact commands for importing a photo, processing a PDF, reviewing uncertain fields, applying a correction, exporting tables, and running evaluation.

In the final report distinguish implemented, actually tested, failed, and blocked work. State the selected default route and evidence for it. Do not claim production accuracy, full multilingual coverage, successful handwriting extraction, or offline readiness without the corresponding measurements.

Make routine reversible implementation decisions autonomously and keep progress updates concise. Request user input only for a concrete missing input, required access, or unresolved scope decision that blocks progress; complete unaffected work first. Do not publish, deploy, connect to the speech project, or send documents to third parties.

Begin with project isolation, actual agent configuration, hardware/runtime inspection, and the smallest genuine local image inference test. Then execute the staged workflow through verified deliverables.

## Primary references to verify against installed versions

- Orca orchestration: https://orca.dev/docs/cli/orchestration
- Orca version-matched guides: https://orca.dev/docs/cli/skills
- OpenAI model/effort guidance: https://developers.openai.com/api/docs/guides/deployment-checklist
- Claude models: https://platform.claude.com/docs/en/models/overview
- Claude Code configuration: https://code.claude.com/docs/en/model-config
- Qwen3.5-9B: https://huggingface.co/Qwen/Qwen3.5-9B
- PaddleOCR-VL-1.6: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
- PaddleOCR Apple Silicon guide: https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.en.md
- MLX-VLM: https://github.com/Neywa-Labs/mlx-vlm
- Optional Qwen3-VL challenger: https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct
