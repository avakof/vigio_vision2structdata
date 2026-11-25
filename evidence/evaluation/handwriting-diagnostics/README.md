# Genuine handwriting diagnostics

On the frozen `bcffc67782a0b786531e6c09d4b5e7583ed63cc8` release candidate, each route produced a reviewable document for all three recipe crops. Hybrid recovered one additional ingredient row. Both routes missed important quantities that a reviewer would need before using the tables. [Sealed specimens, rights and labels](../../../fixtures/handwriting-diagnostics/SOURCES.md)

| Measured on three recipes | Direct Qwen | Full Paddle plus Qwen |
| --- | ---: | ---: |
| Exact labeled fields | 4 / 18 | 6 / 18 |
| Answerable fields with a reading | 3 / 14 | 5 / 14 |
| Correct among answered fields | 3 / 3 | 4 / 5 |
| Correctly aligned labeled ingredient rows | 0 / 5 | 1 / 5 |
| Correct quantity, unit and yield checks | 1 / 9 | 3 / 9 |
| Complete / incomplete processing | 2 / 1 | 2 / 1 |
| Documents sent for review | 3 / 3 | 3 / 3 |
| Automatic acceptances | 0 / 3 | 0 / 3 |
| End-to-end time for the cohort | 82.67 s | 346.78 s |

- **A Seed Cake.** Hybrid recorded the active butter amount as `3` and left its unit out of that ingredient row. Neither route represented the separately crossed-out butter instruction as a structured reading. Direct returned a title and prose description. Measured processing time was 12.56 s direct and 146.85 s hybrid.
- **To Pot Tongues.** Neither route delivered a structured butter row with both endpoints of the handwritten two-to-three-pound quantity. Processing took 53.92 s direct and 117.29 s hybrid.
- **A Rich Plumb Cake.** The scholarly transcription flags the fractional part of the sugar quantity as doubtful. Neither route produced the labeled sugar ingredient row with an ambiguous, null normalized amount. Both recorded the manuscript title. Processing took 12.25 s direct and 81.34 s hybrid.

All labels were frozen at `f6191ca` before diagnostic inference. Both routes used the same sealed sources and settings and ran consecutively in an exclusive inference window. The verifier denied external TCP, permitted loopback and applied the macOS restriction to the CLI and its model subprocesses. This purposive sample has three crops from two English manuscripts. It cannot establish general handwriting accuracy. Zero automatic acceptances leaves the error rate among accepted documents undefined. Case-sensitive name anchors are intentionally strict. The core comparison uses a separate held-out cohort.

The raw scores include every call, failure, status, elapsed time, evidence path and available runtime measurement. The summary also links selected source and normalized readings back to the canonical observations. [Direct run](direct/evaluation.json), [hybrid run](hybrid/evaluation.json), [diagnostic summary](summary.json).
