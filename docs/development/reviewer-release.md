# Independent evidence review of the release candidate

| | |
|---|---|
| Production commit | `bcffc67782a0b786531e6c09d4b5e7583ed63cc8` ("Freeze reconciled release candidate with explicit version lineage") |
| Main inspected | `5812f50` (2026-11-25 19:47 +01:00), merged into `vt-review` as `4b01a3f` |
| Reviewer | Claude Code Opus 5.5 / high, Orca task `task_f8637deb27d5` |
| Scope | Evidence only. No new model inference, no code changes, no held-out tuning, and no document content sent anywhere. Synthetic runs and the final export receipts are tracked separately. |
| Earlier reviews | [review.md](review.md) on `dfa12df` and [review-recheck.md](review-recheck.md) on `36b3200`. Later closures read: [recovery-parser-closure.md](recovery-parser-closure.md), [final-delta-recheck.md](final-delta-recheck.md). |
| Scripts | [`release_check.py`](../../evidence/development/reviewer/release_check.py) recomputes the headline metrics from the committed reports. [`release_example_check.py`](../../evidence/development/reviewer/release_example_check.py) replays the correction example offline. |

## Verdict

The committed measurements match their raw reports, the inputs match their sealed digests, and the release runs used code identical to the frozen commit. The route choice was made before held-out inference. Network access was denied at the operating-system level in every recorded run, and every document went to review.

There is one material exception for gate labelling (E1): the offline CLI extraction receipts and the versioned correction example were produced before the freeze. No other blocking issue was found.

## Checks and results

| Check | Result |
|---|---|
| **Source identity.** `git diff bcffc67 main -- src schemas tests tools scripts fixtures pyproject.toml uv.lock` | **Empty.** Main changes only docs and evidence. I found no test change in `bcffc67..main`, so the "test-budget adjustment" the coordinator mentioned is not visible in that range. |
| **Run commits.** `invocation.json` of the four release evaluations (`043c672`, `38c303c`, `d984ee0`, `c671254`) and the handwriting runs (isolated archive at `bcffc67`) | **0** differing code or fixture files against `bcffc67` for every run. All exit codes are 0. |
| **Tests on merged main.** `.venv/bin/python -m pytest -q tests evidence/development/reviewer` | **371 passed.** This matches the count in `software.json`. All 16 reviewer probes are included. |
| **Input integrity.** Six reports: `manifest_sha256` against the committed manifests, and per-case source and label SHA-256 against the files | **All match** (`release_check.py`). |
| **Shared settings.** One configuration fingerprint per route across development, held-out and handwriting | Direct `b078050b9d7a…`, hybrid `bc83cae4fc6c…`. Both use Qwen revision `938d8919941c`, 4,096 tokens, 1 retry, 1 crop and 2 MP rendering; `components_ready: true`. |
| **Operating-system denial.** Every release and diagnostic `stderr.log` / `offline-probe.log`, `offline-*.json` and the example `verification.json` commands | `{"loopback": true, "public_connect_errno": 1}`: loopback works and the external connection gets `EPERM`. Model children also run under `sandbox-exec (deny network*)` (reviewed earlier). |
| **Route selection before held-out.** Git and receipt chronology | Development trials at `cbc9c05` and `43c092a` (17:52Z and 17:58Z). Selection committed in `2699f16` at 18:09:38Z and the freeze in `bcffc67` at 18:10:29Z. The campaign started at 18:10:49Z, handwriting at 18:11Z and held-out at 18:18Z. Held-out labels were committed at 15:33Z and 16:45Z, handwriting labels in `f6191ca` at 17:44:57Z. No fixture or label changed after the freeze. The frozen development rerun reproduced the selection-trial scores: direct 1/8 and 1/7, hybrid 3/8 and 2/7. |
| **Acceptance report.** `evidence/acceptance.json` at `5812f50` | Gates 0–4 passed, 5 `review_handoff` blocked (pending this review), and `all_requested_routes_complete: false`. Default route is direct. |

## Primary metrics, recomputed from the raw reports

| Cohort (route) | Exact fields | Answerable coverage | Correct when answered | Guessed unresolved | Review / auto-accepted | Case status |
|---|---:|---:|---:|---:|---:|---|
| Development, 3 pages (direct) | 1/8 | 1/7 | 1/1 | 0/1 | 3/3 · 0 | 3 incomplete |
| Development, 3 pages (hybrid) | 3/8 | 2/7 | 2/2 | 0/1 | 3/3 · 0 | 2 completed, 1 incomplete |
| Held-out, 4 sources (direct) | 12/22 | 12/20 | 11/12 | 0/2 | 4/4 · 0 | 3 completed, 1 incomplete |
| Held-out, 4 sources (hybrid) | 11/22 | 12/20 | 11/12 | 0/2 | 4/4 · 0 | 2 completed, 2 incomplete |
| Handwriting diagnostics, 3 crops (direct) | 4/18 | 3/14 | 3/3 | 0/4 | 3/3 · 0 | 2 completed, 1 incomplete |
| Handwriting diagnostics, 3 crops (hybrid) | 6/18 | 5/14 | 4/5 | 0/4 | 3/3 · 0 | 2 completed, 1 incomplete |

Every number above matches `docs/HANDOFF.md`, `evidence/evaluation/handwriting-diagnostics/README.md` and the acceptance selection basis.

There were zero automatic acceptances, so the false-acceptance rate is undefined (0/0) and is reported that way, not as zero. Wall times also match: 494.88 s direct and 555.81 s hybrid for development, and 193.78 s direct and 471.34 s hybrid for held-out.

## Concrete values traced to canonical observations

- **Delmonico's menu (held-out), both routes.** The four labelled dishes are each linked to their printed price: Skate black butter `60`, Sheep's trotters, poulette `60`, Braised beef with noodles `70`, Veal cutlet with tarragon `85`. Each price carries evidence from attempt `p1-qwen-0`, on the `direct` channel and the `parser` channel respectively. The document is still `incomplete` with `reread_structure_disagreement` and ambiguous readings. The scorer's only failure is the source status: labelled `completed`, observed `incomplete`.
- **Carter receipt (held-out).** Both routes read the total as `10`. The printed `$` stays `ambiguous` with a null value, so USD is not assumed. Direct leaves `document_kind` unreadable where the label is `receipt`, and the scorer counts that as a miss, not a guess.
- **The wrong answered field (11/12) is the Bedingfield title on both routes.** Direct read `Potatoe Pudding`, dropping "To Make a". Hybrid read `To Make a Potato Pudding`, which modernises the manuscript's spelling "Potatoe". Spelled-out quantities ("halfe pound", "six") stay `ambiguous` with null values. That is the reason the butter and eggs quantity checks fail, and it is a cautious outcome, not a guessed value.
- **Handwriting, Seed Cake (hybrid).** Butter has quantity `3` with no unit. Four further rows have quantities (`2`, `3`, `43`, …) but `unreadable` names. The document is `incomplete`. Direct produced only the title `A Seed Cake`.
- **Correction example.** v1 (direct) reads `VTX-4187`, `Atelier de la Rive`, `EUR`, `37.25`. v2 (hybrid) splits the total into a second invoice record (`multiple_roots`). The committed `corrections.json` (`remove_row` plus two `add_field`) produces v3 with one root carrying `37.25` and `EUR`, still `needs_review`.
  - Re-applying that file to the exported v2 with current domain code reproduces the exported v3 exactly.
  - Manifests, snapshot SHA-256, and JSON, CSV and SQLite observations and entities agree for v1–v3.
  - v2 still contains the removed row (`release_example_check.py`).

## Material exception

**E1 · The production-route CLI receipts and the correction example predate the frozen code.**

- `evidence/verification/offline-direct.json` and `offline-hybrid.json` were recorded at `fa1aa9e` (17:23Z). The example's v1/v2 snapshots and the wheel reimport (`installed_wheel_sha256 82632876…`, not the frozen wheel `de5b02e5…` in `software.json`) were committed by 17:51Z.
- Between `fa1aa9e` and `bcffc67`, `vision.py` changed in 636 lines, along with `config.py`, `domain/build.py` and `cli.py`.
- The recorded runs used `max_tokens 2048`, whereas release defaults are 4,096.
- `acceptance.json` nevertheless lists `production_routes.direct/hybrid: passed` and `offline_production: passed` citing these files.
- Real inference on the frozen code is evidenced only through `verify_offline.py evaluate`, which runs `pipeline.extract` without Store persistence or exports. The development replay (`development-replay.json`) re-ran stored model replies on `bcffc67` but intercepted inference.
- **Effect:** the persistence, correction and export path has not been exercised end to end with real inference on the frozen commit. The coordinator says final export receipts continue separately. Until they land, `production_routes` and `offline_production` should cite those receipts or state that they come from a pre-freeze commit.

## Non-blocking observations and limits

- **Sample size.** The headline cohorts have 8, 22 and 18 labelled fields over 3, 4 and 3 sources: two English manuscripts for handwriting and largely historical material elsewhere. The documentation states this. Hybrid's +2 on development and −1 on held-out exact fields are within noise for these denominators. The choice of direct as default rests on cost (wall time and peak child RSS of 7.48 GB direct versus 14.03 GB hybrid at selection), which `evaluation-settings.md` says plainly.
- **Source spelling.** Neither route guarantees verbatim source spelling: hybrid modernised "Potatoe". The raw readings come from the model and are flagged only when evidence checks fail. Reviewers must compare against the page.
- **"valid" does not mean checked.** A document can have `validation: valid` while carrying `info`-level structure notices. The hybrid example v2 had two invoice roots and `multiple_roots`. Review is still mandatory, but readers should not treat "valid" as "checked".
- **Earlier recheck nit resolved.** `README.md:30` no longer over-claims, and HANDOFF now reports measured results with denominators.
- **Not re-run by me:** model inference, timing, RSS, the synthetic campaign, and the final export receipts.
