# Development reply recovery

The checks replayed the two named public development jobs and used synthetic grammar cases. The runtime boundary returned the original saved replies. No model weights were loaded, and no inference or held-out examples were used.

## Saved reply replay

| Job and attempt | Termination | Retained records | Readable / ambiguous / unreadable fields | Structural recovery codes |
|---|---|---:|---|---|
| `9396e18b…`, 0 | Stop, 1,221 tokens | 4 | 19 / 0 / 3 | `model_field_omitted`, `model_record_omitted` |
| `9396e18b…`, 1 | Stop, 1,092 tokens | 5 | 28 / 0 / 0 | `model_record_id_recovered` |
| `b0c7f45f…`, 0 | Length, 2,048 tokens | 8 | 8 / 8 / 1 | `model_prefix_recovered`, `model_field_duplicate` |
| `b0c7f45f…`, 1 | Length, 2,048 tokens | 5 | 5 / 6 / 0 | `model_prefix_recovered`, `model_field_duplicate` |

The two-attempt page adapter kept 5 records for `9396e18b…` and 8 for `b0c7f45f…`. Both pages were incomplete. The first retained 27 readable and 1 ambiguous fields. The second retained 9 readable and 8 ambiguous fields. The replay checked both individual replies with `_payload` and the public page adapter with those replies supplied in their original order. It reported counts and structural codes only.

Recovery requires a bounded strict JSON prefix ending at EOF and a measured token count equal to the configured cap. It keeps only closed records, validates each retained field and rejects duplicate JSON object keys. Exact repeated fields can consolidate within one occurrence. Competing candidate values stay unresolved. Local record-ID recovery preserves physical occurrences and requires a unique compatible parent. The assembler suppresses default root joining only when the selected adapter attempt explicitly reports the parent as unresolved.

## Offline grammar and focused tests

The installed `mlx_vlm.structured` decoder compiled the invoice, menu, auto and recipe grammars against the verified local Qwen tokenizer. With the main offline sandbox profile, the check called `prepare_next_token_mask` on each synthetic sequence and checked the final EOS mask. All four valid paths passed, including a rootless continuation. Token masks rejected all six invalid paths covering mismatched family, wrong field for the record kind, repeated root, region ID as parent, forged evidence region and forged geometry. The scratch mask script, schema copies and output are under `.vt-state/development-recovery/` in `vt-vision`.

```sh
.venv/bin/pytest -q tests/test_vision_development_recovery.py tests/test_vision.py \
  tests/test_vision_parser_tokens.py tests/test_qa_evidence_metadata.py tests/test_domain_assemble.py
```

The 109 focused tests passed. They include malformed and duplicate-key prefixes, conflicting duplicate prices, unique typed ID recovery, orphan suppression, preserved absent-parent auto-joining and section-safe reread matching.
