# Independent frozen-candidate evaluation audit

I audited the four recorded evaluations against `bcffc67782a0b786531e6c09d4b5e7583ed63cc8`. I rebuilt every case count, failure location and stratum aggregate from the committed labels and canonical result snapshots, then checked the stored rates. The four reports reconciled. The audit used the existing artifacts and ran no inference.

| Cohort and route | Documents emitted | Case status `completed` | Cases passing every label check | Field exact | Answerable coverage | Exact among answered | Labeled rows | Menu price | Critical numeric | Recipe quantity, unit and yield | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Primary direct | 4/4 | 3/4 | 0/4 | 12/22 | 12/20 | 11/12 | 4/8 | 4/4 | 1/1 | 1/6 | 193.78 |
| Primary hybrid | 4/4 | 2/4 | 0/4 | 11/22 | 12/20 | 11/12 | 4/8 | 4/4 | 1/1 | 0/6 | 471.34 |
| Handwriting diagnostic direct | 3/3 | 2/3 | 0/3 | 4/18 | 3/14 | 3/3 | 0/5 | — | — | 1/9 | 82.67 |
| Handwriting diagnostic hybrid | 3/3 | 2/3 | 0/3 | 6/18 | 5/14 | 4/5 | 1/5 | — | — | 3/9 | 346.78 |

Every emitted document required review, 4/4 per primary route and 3/3 per diagnostic route. There were zero automatic accepts among the four primary or three diagnostic ineligible cases on each route. False acceptance has numerator 0 and denominator 0, so its rate is undefined. The relationship metric likewise has no labeled checks.

- **Delmonico’s**. On both routes, each of the four expected name anchors matched exactly one canonical row. The corresponding page-one numeric prices were 60, 60, 70 and 85. The case remained incomplete on both routes, so 4/4 checks the labeled pairs on that page.
- **Primary recipe yields**. Direct received the single additional exact match for Lutz–Messmer. Its completed recipe had an identified root, page evidence, `recipe_yield_missing` and no conflicting yield observation or capture warning. Hybrid left the page incomplete. Both Bedingfield outputs had the missing-yield issue but received no credit because capture was suspected incomplete.
- **Handwriting diagnostics**. Both routes earned the supported missing-yield credit on Plumb Cake, and hybrid also earned it on Potted Tongues. Absence credit did not add to answerable coverage. Neither route earned credit for the Plumb Cake sugar quantity because neither produced a unique exact sugar-row anchor for the labeled ambiguous quantity. Hybrid correctly matched the active butter row and quantity 3 on Seed Cake. The struck-out butter row and Potted Tongues range endpoints received no credit on either route.

Primary receipt SHAs were `043c6727c994376713ea5ef5b9df5e7a4937101e` and `38c303cd355db58c3f0430e5a6af107493cfd36c`. Their application source, wrapper, dependency lock and primary fixtures are byte-equivalent to the freeze. Both diagnostics recorded the frozen application archive directly. I checked 42 relevant main paths and all 41 matching paths present in the archived candidate against the frozen Git blobs.

The primary manifest SHA-256 was `e259c0680137a689640073368d9abcd29f5bad727610ee12e667a96e9dffb021`. The diagnostic manifest SHA-256 was `28bc671002b55afc9e103b7abd0a42e1fbb6683c1187b054c45a011bb78a130e`. Source and label digests matched the reports and committed bytes for every case. The development, primary and diagnostic manifests have disjoint case IDs, declared families and source digests. Every case used default options. Canonical options and fingerprints matched their reports. The primary routes made seven direct Qwen calls and eight hybrid Qwen calls, and the diagnostic routes made four and seven respectively. All used the same Qwen revision and 4,096-token budget. The hybrid route used the declared 32-region detector bound and 768 recognition tokens per crop. The saved macOS sandbox probes showed successful loopback and public connection denial with errno 1.

The primary cohort has four sparsely labeled archival pages, including one French manuscript. The three English diagnostic crops come from two historical manuscripts selected for specific handwriting failure modes. Exact pairs and quantities apply to these labels. Complete document accuracy, broader handwriting performance, false acceptance under an active acceptance policy and cold-versus-warm performance remain unverified.

[Primary direct](../evaluation/release/direct-heldout/evaluation.json) · [Primary hybrid](../evaluation/release/hybrid-heldout/evaluation.json) · [Handwriting diagnostics](../evaluation/handwriting-diagnostics/README.md)
