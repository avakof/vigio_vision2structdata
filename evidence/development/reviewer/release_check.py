"""Recompute release headline numbers from committed evaluation reports (no inference).

Run: .venv/bin/python -I evidence/development/reviewer/release_check.py   (from the repository root)
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path.cwd()
RUNS = {
    "direct-development": ("evidence/evaluation/release/direct-development", "fixtures/public/manifest.json"),
    "hybrid-development": ("evidence/evaluation/release/hybrid-development", "fixtures/public/manifest.json"),
    "direct-heldout": ("evidence/evaluation/release/direct-heldout", "fixtures/archive-heldout/manifest.json"),
    "hybrid-heldout": ("evidence/evaluation/release/hybrid-heldout", "fixtures/archive-heldout/manifest.json"),
    "direct-handwriting": ("evidence/evaluation/handwriting-diagnostics/direct", "fixtures/handwriting-diagnostics/manifest.json"),
    "hybrid-handwriting": ("evidence/evaluation/handwriting-diagnostics/hybrid", "fixtures/handwriting-diagnostics/manifest.json"),
}


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


for name, (folder, manifest_path) in RUNS.items():
    report = json.loads((ROOT / folder / "evaluation.json").read_text())
    manifest = json.loads((ROOT / manifest_path).read_text())
    base = (ROOT / manifest_path).parent
    digest_ok = report["manifest_sha256"] == sha(manifest_path)
    files_ok = all(sha(base / c["source"]) == c["sha256"] and sha(base / c["labels"]) == c["labels_sha256"]
                   for c in manifest["cases"])
    total = Counter()
    for case in report["case_results"]:
        total.update(case["counts"])
    cfg = report.get("configuration", {})
    calls = sum(len(r.get("calls", [])) for c in report["case_results"]
                for r in c.get("runtime_sample", {}).get("backend", {}).get("runs", []))
    print(f"{name}: manifest_digest_match={digest_ok} sources_labels_match={files_ok} "
          f"route={cfg.get('pipeline')} max_tokens={cfg.get('max_tokens')} model_rev={str(cfg.get('revision'))[:12]} "
          f"cfg={str(cfg.get('base_config_hash'))[:12]} ready={cfg.get('components_ready')}")
    print(f"   cases={[(c['fixture'], c['status']) for c in report['case_results']]}")
    print(f"   field_exact={total['field_exact_ok']}/{total['field_exact_n']} "
          f"answerable_covered={total['covered_answerable_fields']}/{total['answerable_fields']} "
          f"correct_when_answered={total['correct_answered_fields']}/{total['covered_answerable_fields']} "
          f"guessed_unresolved={total['guessed_unresolved_fields']}/{total['unresolved_fields']} "
          f"review_docs={total['review_documents']}/{total['documents_with_known_approval']} "
          f"auto_accepted={total['auto_accepted_documents']} false_accepted_cases={total['false_accepted_cases']} "
          f"model_calls={calls}")
