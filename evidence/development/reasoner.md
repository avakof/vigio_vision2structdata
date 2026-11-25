# Independent reasoner evidence for Gate 2

The [audit](../../docs/development/contract-audit.md) reviews the initial request and prose at `db3321d9d0a8a0a585558223a80609189023ab1e` and the typed main draft at `e55f50e66b7acf5652eb35a3e100c29b365fc147`. The latter supplies `src/vision_tables/contracts.py`, `src/vision_tables/config.py` and `docs/development/interfaces.md`. These are the review targets even if main advances.

| Check | Result | Interpretation |
| --- | --- | --- |
| Independent review assignment | passed | Examined the stage APIs, settings, types and failure cases. Six findings and their acceptance checks are in the audit. |
| Gate 2 freeze at the typed draft | failed | Reproduced ambiguous JSON becoming a single reading and a snapshot approving itself without observations. Correction and domain contracts also need explicit outcomes. |
| Inference, parser availability, real-document quality, offline operation, database transactions and export replay | not_run | The assignment concerns the preimplementation contract. Acceptance scenarios in the audit are specifications unless identified below as executed. |

## Session provenance

| Field | Recorded value |
| --- | --- |
| Requested role, harness, model and effort | Independent reasoner, Codex, `gpt-6.2-astra`, `xhigh`. The project request and main's `evidence/development/launch/reasoner.json` agree. |
| Effective harness | The running turn's `session_meta` recorded `originator=orca_desktop`, `model_provider=openai`, `cli_version=0.171.0`. Local `codex --version` also returned `codex-cli 0.171.0`. |
| Effective turn | The harness journal's `turn_context` at `2026-11-25T15:01:21.065Z` recorded `model=gpt-6.2-astra`, `effort=xhigh` and turn `01a21339-acbe-76c1-941b-afa03a37cb04`. No substitution appeared between launch and this turn. |
| Codex thread | `01a21339-ac34-7863-8028-c56b2f332efe`. Matched the journal's session ID to the running thread and the working directory. Only scalar provenance fields were retained. |
| Orca | Local Orca 1.4.274 (bundle metadata). Resolved `ORCA_CLI_COMMAND` to `/Applications/Orca.app/Contents/Resources/bin/orca`. Loaded its full orchestration guide. Status reported the app running. |
| Orca run, task and dispatch | `run_7901ade1ddff`, `task_1a04b12a5d27`, `ctx_f8fd0b9bbfdb`. Matched using `orchestration worker-show`. |
| Orca worker and sessions | Worker `structworker_1b0ab809-6b0b-469a-939e-88bfa5522096`, session `6bb80f99-cd2d-46b1-842d-dbd4079bf178`, coordinator `86695985-f792-4d5b-9f14-8b8109e83a35`. |
| Placement and ownership | Local branch `vt-reasoner` at `/Users/vakof/orca/workspaces/vision-to-tables/vt-reasoner`. The dispatch names this machine and permits the audit and this evidence file. |

## Executed wire probes

Python 3.12.16, Pydantic 2.14.4 and pydantic-core 2.50.5. Read the two Python modules directly from the typed Git revision and loaded them in memory with the pinned packages from the project cache. No project installation or source changes were needed. To reproduce in an installed checkout of that revision, run the block with its Python interpreter and `PYTHONPATH=src`. Run the same cases at the final public boundaries after freeze. A different result in a command or assembler can demonstrate a higher-level guard if that guard is also enforced on every equivalent entry point.

```python
from copy import deepcopy
from pydantic import ValidationError
from vision_tables.contracts import Correction, CorrectionSet, Document, FieldRead, PageReading

base = dict(
    document_id="doc", doc_type="invoice", scope=[1],
    source=dict(sha256="0"*64, media_type="image/png", size_bytes=100, page_count=1,
                original_path="original.png"),
    pages=[dict(number=1, width=100, height=200, unit="px", image_path="page.png",
                image_width=100, image_height=200, image_to_original=[1, 0, 0, 1, 0, 0])],
    regions=[], entities=[dict(id="row", kind="line", page=1, ordinal=0)],
    observations=[dict(id="amount", entity_id="row", field="amount", raw="8.50",
                       value="8.50", state="readable", evidence=[dict(page=1, quote="8.50")])],
    run=dict(job_id="job", pipeline="direct", model="fixture", revision="fixture",
             runtime={}, settings={}, config_hash="1"*64),
    coverage=dict(processed_pages=[1]), issues=[], validation="valid"
)
def check(label, validator, data):
    try:
        validator(data)
    except ValidationError:
        print(label, "rejected")
    else:
        print(label, "accepted")

def change(obs=None, **fields):
    d = deepcopy(base)
    d.update(fields)
    if obs:
        d["observations"][0].update(obs)
    return d

document = Document.model_validate
check("ordinary", document, base)
check("cross-page evidence", document, change(obs=dict(evidence=[dict(page=2)])))
check("entity cycle", document, change(entities=[dict(id="row", kind="line", page=1,
                                                       ordinal=0, parent_id="row")]))
check("self-authorized empty", document, change(entities=[], observations=[], approval="human_approved"))
check("suspected with no issue", document, change(coverage=dict(processed_pages=[1],
      original_capture="suspected_incomplete"), approval="human_approved"))
check("ambiguous without alternatives or reason", document, change(obs=dict(state="ambiguous", value=None)))
check("empty readable evidence", document, change(obs=dict(raw=None, value=None, evidence=[dict(page=1)])))
check("duplicate effective field", document, change(observations=[
    base["observations"][0], dict(base["observations"][0], id="other", value="6.50")]))
check("skipped and incomplete with observation", document, change(coverage=dict(
    processed_pages=[], skipped_pages=[1], incomplete_pages=[1]), validation="incomplete"))
check("empty reading", PageReading.model_validate, dict(page=1, doc_type="invoice"))
print("duplicate scalar", FieldRead.model_validate_json('{"raw":"8.50","raw":"6.50"}').raw)
event = dict(document_id="doc", base_version=1, reviewer="R")
edit = dict(observation_id="amount", value=1, reason="review")
print("numeric correction", repr(Correction.model_validate(edit).value))
check("approval without a field edit", CorrectionSet.model_validate, dict(**event, changes=[], approve=True))
check("two edits to one target", CorrectionSet.model_validate, dict(**event, changes=[edit, edit]))
check("wrong lineage", document, change(version=2, parent_version=3))
check("unrecognized field", document, change(obs=dict(field="irrelevant_measure", value="NaN")))
```

| Probe | Observed |
| --- | --- |
| Ordinary, cross-page evidence, entity cycle | Accepted ordinary. Rejected unknown evidence page and cyclic parent. |
| Self-authorized empty, suspected with no issue | Accepted both as `human_approved`. |
| Ambiguous without alternatives or reason, empty readable evidence | Accepted both. |
| Duplicate effective field, skipped and incomplete with observation, empty reading | Accepted all three. |
| Duplicate scalar, numeric correction | Returned `"6.50"` and Python `True`, respectively. |
| Approval without a field edit, two edits to one target | Rejected the approval-only request and accepted repeated edits. |
| Wrong lineage, unrecognized field | Accepted a child with a future parent and an arbitrary field with value `"NaN"`. |

Separate in-memory stdlib checks confirmed that ordinary JSON dictionary decoding retains only the last duplicate key, default decoding accepts `NaN`, and `Decimal("0.335")` rounded half-up per line versus on a three-line sum produces `1.02` versus `1.01`. These are parser/arithmetic examples. The schema results above ran the actual draft validators.

The freeze lesson is to assign each fact to an authoritative writer. The model can propose a reading, the adapter knows whether generation stopped early, the source inventory knows what was skipped, and a stored human event can authorize approval. An immutable snapshot must retain enough identity and provenance to check all four independently.
