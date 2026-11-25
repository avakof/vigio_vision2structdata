# Development timeline

Times are local to the target Mac on 2026-11-25 (Europe/Paris). Launch receipts and handoffs supply the exact Git and Orca IDs.

| Time | Observed event |
|---|---|
| 15:59 | Lead session and MLX provisioning started on the independent repository. |
| 16:01 | Astra/xhigh architect, Sol/high native runtime engineer and Sol/high QA engineer launched in separate Orca worktrees. |
| 16:09–16:11 | Initial full Paddle run stopped at the incomplete-weight check. Native package and component provisioning resumed. The architect reported reproducible contract failures on the draft. |
| 16:13–16:14 | Lead froze the revised model payload, vocabulary, provenance, coverage and correction interfaces. Vision implementation resumed. Claude Sonnet/high domain and persistence sessions launched as QA and the architect finished their first assignments. |
| 16:21 | The 130,806,572-byte layout detector was reconstructed from resumable HTTP ranges and verified against its pinned SHA-256. |
| 16:25 | Image preprocessing and constrained-grammar construction passed under the external-network restriction after pinning torch and torchvision as processor dependencies. No model weights were executed in this check. |
| 16:28–16:30 | Vision adapters and persistence were integrated. The lead reran the 15 vision tests and 74 persistence/CLI tests. Native ImageIO encoded and decoded an 800 × 420 static HEIC, prompting an implementation follow-up. |
| 16:31–16:38 | Integrated the domain package, document store, HEIC and source-specific evidence. The first full integrated suite passed 213 tests. QA and Opus began independent checks. |
| 16:41–16:43 | QA reproduced sign, coverage and label defects. Opus reproduced versioning, export, review and mixed-PDF defects on dfa12df. |
| 17:04–17:16 | Resumed interrupted transfers after host sleep and integrated fixes for the reproduced defects, structural corrections and scorer labels. The static-image and document cases stayed local. |
| 17:24 | Opus rechecked the integrated 36b3200. All 294 package tests and 16 reviewer probes passed. Both end-to-end model runs still awaited pinned weights. |

The contracts, evaluation harness and implementation progressed while the initial multi-gigabyte downloads were pending. Gate 1 inference had not passed when the implementation sessions started. Neither the worker receipts nor their tests establish route readiness; the final gate report records later production runs separately.
