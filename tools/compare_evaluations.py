"""Compare two scored local CLI runs of the same fixture manifest and settings."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

# The model and operating bounds must match. Runtime components and extraction
# fingerprints differ by design between direct and hybrid.
SHARED = (
    "doc_type", "page_groups", "max_bytes", "max_pages", "max_pixels",
    "render_pixels", "max_crops", "max_tokens", "retries", "model", "revision",
)
MEASURES = (
    "field_exact", "answerable_field_coverage", "accuracy_when_answered",
    "critical_numeric", "row_alignment", "relationship", "menu_price",
    "recipe_quantity_unit_yield", "review_rate", "false_acceptance_rate",
    "unresolved_guess_rate", "case_pass",
)


def case_identity(case):
    return tuple(case[key] for key in ("fixture", "origin", "split", "sha256", "labels_sha256", "settings"))


def runtime(cases):
    calls = [call for case in cases
             for page in case["runtime_sample"]["backend"].get("calls_by_page", {}).values()
             for call in page]
    qwen = [call for call in calls if call.get("constrained")]
    parser = [call for call in calls if call.get("channel") == "parser" and call not in qwen]
    crops = [crop for call in parser for crop in (call.get("recognition_tokens") or {}).get("crops", [])]
    return {
        "case_seconds": [round(case["runtime_sample"]["seconds"], 3) for case in cases],
        "case_status": dict(Counter(case["status"] for case in cases)),
        "emitted_documents": sum(case["counts"]["documents"] for case in cases),
        "attempted_pages": sum(len(case["runtime_sample"]["backend"].get("attempted_pages", [])) for case in cases),
        "processed_pages": sum(len(case["processed_pages"]) for case in cases),
        "qwen_calls": len(qwen),
        "qwen_tokens": sum(call.get("generated_tokens", 0) for call in qwen),
        "qwen_token_cap_hits": sum(bool(call.get("overflow")) for call in qwen),
        "qwen_crop_reads": sum("crop_region_id" in call for call in qwen),
        "qwen_retry_and_crop_calls": sum(call.get("id", "").rsplit("-", 1)[-1] != "0" for call in qwen),
        "parser_calls": len(parser),
        "parser_crops_with_token_counts": len(crops),
        "parser_tokens": sum(crop["generated_tokens"] for crop in crops),
        "parser_token_cap_hits": sum(crop["cap_hit"] for crop in crops),
        "max_model_process_rss_bytes": max((call.get("peak_rss_bytes", 0) for call in calls), default=0) or None,
        "max_mlx_peak_bytes": max((call.get("mlx_peak_bytes", 0) for call in qwen), default=0) or None,
    }


def compare(direct, hybrid):
    reports = {"direct": direct, "hybrid": hybrid}
    for route, report in reports.items():
        if report["configuration"]["pipeline"] != route:
            raise ValueError(f"Expected {route} evaluation")
    if direct["manifest_sha256"] != hybrid["manifest_sha256"]:
        raise ValueError("Fixture manifests differ")
    shared = {key: direct["configuration"][key] for key in SHARED}
    if any(hybrid["configuration"][key] != value for key, value in shared.items()):
        raise ValueError("Model or shared configuration differs")
    if ([case_identity(case) for case in direct["case_results"]]
            != [case_identity(case) for case in hybrid["case_results"]]):
        raise ValueError("Cases, labels, overrides or order differ")
    output = {"manifest_sha256": direct["manifest_sha256"], "shared": shared, "routes": {}}
    for route, report in reports.items():
        groups = []
        for group in report["metrics"]:
            groups.append({
                "origin": group["origin"], "split": group["split"], "stratum": group["stratum"],
                "cases": group["counts"]["attempts"],
                "measures": {name: [group["rates"][name]["numerator"], group["rates"][name]["denominator"]]
                             for name in MEASURES if name in group["rates"]},
            })
        cases = report["case_results"]
        output["routes"][route] = {
            "case_order": [case["fixture"] for case in cases],
            "runtime": runtime(cases), "groups": groups,
        }
    return output


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("direct", type=Path, help="Direct evaluation.json")
    ap.add_argument("hybrid", type=Path, help="Hybrid evaluation.json")
    args = ap.parse_args()
    print(json.dumps(compare(json.loads(args.direct.read_bytes()), json.loads(args.hybrid.read_bytes())),
                     ensure_ascii=False, indent=2, allow_nan=False))
