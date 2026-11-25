# Recovery parser closure at `bcffc67`

**All five recovery findings closed on the synthetic reproductions.** Reviewed `bcffc67782a0b786531e6c09d4b5e7583ed63cc8` (pipeline 0.1.3). After merging main, the audit worktree and reviewed commit shared Git tree `5a824845b707044b54553799541e22b237a4774e`. [Original findings](recovery-parser-audit.md) · [Unmodified independent probes](../../evidence/development/reasoner_recovery_probes.py)

| Case | Observed result |
| --- | --- |
| Soup and Salad become two Soups on retry | Both original rows survived. The canonical document reported `reread_structure_disagreement`, page 1 incomplete and approval blocked. |
| Unresolved parent | Paper retained a null parent after the larger retry. With changed local IDs, the effective unresolved ID followed Paper to `r2` and Ink at `r1` joined the invoice. The indistinguishable-duplicate control also preserved the unresolved occurrence. |
| Contested price | Soup stayed null and `ambiguous` with prices 4, 7 and 9. The stored evidence retained all three quotes from both attempts. Bread's price 3 stayed `readable`. The order and extra-row controls passed. |
| Conflicting native totals | The stored total stayed null and `ambiguous`, with alternatives 4.00 and 7.00. Its citations retained `span-a` / `Total 4.00` and `span-b` / `Total 7.00`, both on channel `native_text` from attempt `p1-qwen-0`. |
| Alternative limit | Page JSON revalidated with 16 distinct candidates and the stored price stayed null and `ambiguous`. The retry proposing 17 recorded one distinct omission and kept 17 in its attempt data. Repeating 16 counted zero omissions. |
| Duplicate Soup rows with prices 4 and 6 | Retry prices **7, 4** yielded a structure disagreement, incomplete page 1 and blocked approval. Retry prices **6, 4** yielded a valid document eligible for review approval. Both stored documents retained two separate readable prices, 4 and 6, each citing only its own first-attempt reading. |

## Commands and results

Run from the repository root in the local core environment.

```sh
py=/Users/vakof/orca/vision-to-tables/.venv/bin/python
PYTHONPATH=src "$py" -B evidence/development/reasoner_recovery_probes.py
PYTHONPATH=src "$py" -B -m pytest -q -p no:cacheprovider \
  tests/test_vision_development_recovery.py \
  tests/test_audit_regressions.py::test_strict_json_and_semantic_boundaries \
  tests/test_vision.py::test_valid_json_overflow_and_reread_disagreement \
  tests/test_vision.py::test_native_invented_amount_abstains
```

The independent script passed all eight groups, including 526 prefix boundaries, exact-cap and short-stop handling, strict input bounds and invalid references. The targeted pytest command reported **29 passed in 0.73 s**.

I also ran the five original failures and both duplicate-price cases through seven fresh SQLite stores using `PYTHONPATH=src:tests:evidence/development "$py" -B -` with an inline wrapper around assembly. The wrapper supplied a real store job ID, called `persist_candidates`, reloaded each document with `get_document`, and compared the whole snapshot with the assembled object. All seven matched. Queries against `entities`, `observations` and `evidence_links` also matched every parent ID, field state and value, and ordered region, quote, channel and attempt ID. No model inference or held-out data was used. Route quality remains a separate measurement.
