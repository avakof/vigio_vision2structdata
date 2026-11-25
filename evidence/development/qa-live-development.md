# Offline public development evaluation

Each route ran once on the three public development images using the installed main CLI. The direct run finished before hybrid started. The independent hybrid product smoke receipt was present before either cohort launched and recorded exit 0. Both evaluations used the requested shared state and inference lock. The offline probes observed successful loopback traffic and public connection denial with errno 1.

| Route | Evaluated commit | Invocation | CLI result |
| --- | --- | --- | --- |
| Direct | `9511437126ff2598e3b1603a198c5c2217bcf434` | [Direct receipt](../evaluation/direct-development/invocation.json) | [Exit JSON](../evaluation/direct-development/exit.json), [offline probe](../evaluation/direct-development/stderr.log) |
| Hybrid | `e34f65cad5701c06f16858dc2ad8f9f5b9fff6a4` | [Hybrid receipt](../evaluation/hybrid-development/invocation.json) | [Exit JSON](../evaluation/hybrid-development/exit.json), [offline probe](../evaluation/hybrid-development/stderr.log) |

The manifest SHA-256 was `c20d4aa88f909bf0a6b3c3aaebb1d43d789b8411501d9847c7a1b97f54c65261` for both runs. The executable diff between the two commits adds a fallback top-level CLI status only when a handler supplies none. The evaluation handler supplies a status. Source manifests, extraction, scoring, runtime, environment lock and offline wrapper were identical between those commits.

Both routes used automatic type detection, a 2,000,000 rendered pixel budget, 40,000,000 maximum source pixels, a single reread crop, one retry and a 2,048-token Qwen generation limit. The hybrid layout detector bounded downstream work to 32 regions after layout. The Paddle recognizer was limited to 768 generated tokens per crop. Qwen used `mlx-community/Qwen3.5-9B-MLX-4bit` at revision `938d8919941c6e7efd3c7150eff7fe9d12afa631`. Full configuration and call telemetry appear in the two evaluation reports.

## Outcomes

Both CLI invocations exited 0 and generated reports. All six evaluated document attempts were incomplete with `no_extraction`. Neither route assembled a document from the archival cases. The independent offline smoke used a different input.

| Measure | Direct | Hybrid |
| --- | ---: | ---: |
| Complete case results | 0/3 | 0/3 |
| Document type exact match | 0/3 | 0/3 |
| Field exact match | 0/8 | 0/8 |
| Answerable field coverage | 0/7 | 0/7 |
| Accuracy among answered fields | Undefined (0 answers) | Undefined (0 answers) |
| Critical invoice numeric field | 0/1 | 0/1 |
| Menu price | 0/1 | 0/1 |
| Recipe quantity, unit and yield | 0/3 | 0/3 |
| Labeled row alignment | 0/2 | 0/2 |
| Relationship checks | Undefined (0 labels) | Undefined (0 labels) |
| Automatic acceptances on ineligible cases | 0/3 | 0/3 |

Review rate, false acceptance rate and end-to-end seconds per processed page are undefined because neither route emitted any documents or processed pages. Source status, page coverage and the breakdown by stratum are preserved in the original [direct evaluation report](../evaluation/direct-development/evaluation.json) and [hybrid evaluation report](../evaluation/hybrid-development/evaluation.json).

## Calls and elapsed time

| Public case | Direct seconds | Direct Qwen generated tokens | Hybrid seconds | Hybrid Qwen generated tokens | Paddle layout regions | Paddle recognition crops returned/requested | Paddle seconds |
| --- | ---: | --- | ---: | --- | ---: | ---: | ---: |
| `babani-1921` | 54.40 | 1,221 / 1,092 | 172.16 | 1,440 / 1,435 | 10 | 4/4 | 105.75 |
| `le-versailles-1920-p1` | 103.75 | 2,048 / 2,048 | 145.87 | 2,048 / 2,048 | 11 | 9/9 | 60.25 |
| `jefferson-ice-cream` | 96.96 | 2,048 / 876 | 166.59 | 2,048 / 867 | 8 | 3/3 | 99.20 |

Direct made six constrained Qwen calls in 255.36 seconds wall time. Hybrid made three native CPU Paddle calls and six constrained Qwen calls in 486.11 seconds. All 16 requested Paddle crops returned with complete token telemetry, zero token-cap hits and zero omitted or degraded regions. The highest recognition count was 373 tokens. The pipeline rejected all 12 Qwen replies. For each route, three replies across the menu and recipe cases hit Qwen's cap and were invalid JSON. For the other three direct replies, the first observed rejection was a duplicate field twice and a duplicate local record ID once. For the other three hybrid replies, two failed on duplicate local record IDs and one failed on a duplicate field. These causes were checked against local attempt artifacts using strict JSON parsing and the production adapter's validators. No model was called for that diagnosis.

| Peak memory measure | Direct | Hybrid |
| --- | ---: | ---: |
| Evaluator process RSS | 162.0 MiB | 155.2 MiB |
| Qwen worker RSS | 6.961 GiB | 6.966 GiB |
| Qwen MLX allocator peak | 7.162 GiB | 7.166 GiB |
| Paddle worker RSS | Not applicable | 13.067 GiB |

These peaks are measured separately and can overlap on unified memory. Each model call used a fresh subprocess. The route wall times include runtime inventory validation, launch, retries and I/O. The per-case timing in the reports begins at extraction and carries separate model load, generation and worker runtime timings where available. No held-out or synthetic evaluation was performed in this dispatch. These three archival development pages provide a measured failure result under the production defaults.
