"""vision-tables command line. Every command prints one JSON object on stdout; progress is JSON lines on
stderr (silence with --no-progress). The exit code is also in the object as ``exit_code``.

Exit codes: 0 ok · 1 internal error · 2 usage · 3 incomplete document(s) · 4 failed · 5 missing local
model/runtime · 6 conflict (stale base or lost job lease) · 7 invalid input/reference · 8 persisted but
export pending (run ``jobs recover``) · 130 cancelled.
"""
from __future__ import annotations

import argparse
import json
import re
import signal
import sys
import threading
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from . import exports, store_jobs
from .config import ProcessingError, Settings, default_state
from .contracts import CorrectionSet
from .store import DEFAULT_FORMATS, Store
from .store_jobs import (EXIT_CANCELLED, EXIT_EXPORT_PENDING, EXIT_INTERNAL, EXIT_MISSING_RUNTIME, EXIT_OK,
                         EXIT_USAGE, error_payload, exit_code)
from .wire_json import parse_object

CORRECTION_BYTES = 8 << 20


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # usage errors are machine-readable too
        self.print_usage(sys.stderr)
        print(json.dumps({"status": "usage_error", "exit_code": EXIT_USAGE,
                          "error": {"code": "usage", "message": message, "next": f"See '{self.prog} --help'."}},
                         indent=2), flush=True)
        raise SystemExit(EXIT_USAGE)


MAX_GROUP_PAGES = 1000  # expanded indices per --page-group; far above max_pages, far below memory trouble


def _groups(values: list[str] | None) -> tuple[tuple[int, ...], ...]:
    groups = []
    for text in values or []:
        pages: list[int] = []
        for part in text.split(","):
            match = re.fullmatch(r"\s*(\d+)(?:-(\d+))?\s*", part)
            if not match:
                raise ValueError(f"invalid page group '{text}': use e.g. 1,2 or 3-5 (one-based, source order)")
            low, high = int(match[1]), int(match[2] or match[1])
            if high < low:
                raise ValueError(f"invalid page range '{part}': the end page precedes the start")
            if high - low + 1 + len(pages) > MAX_GROUP_PAGES:
                raise ValueError(f"page group '{text}' names more than {MAX_GROUP_PAGES} pages; list only the "
                                 "source pages that form one document (at most --max-pages are read)")
            pages.extend(range(low, high + 1))
        groups.append(tuple(pages))
    return tuple(groups)


def _settings(args) -> Settings:
    names = ("pipeline", "max_bytes", "max_pages", "max_pixels", "render_pixels", "max_crops", "max_tokens", "retries")
    kwargs = {n: getattr(args, n) for n in names if getattr(args, n, None) is not None}
    if getattr(args, "type", None):
        kwargs["doc_type"] = args.type
    if getattr(args, "state_dir", None):
        kwargs["state_dir"] = Path(args.state_dir)
    if getattr(args, "lock_file", None):
        kwargs["lock_file"] = Path(args.lock_file)
    try:
        if getattr(args, "page_group", None):
            kwargs["page_groups"] = _groups(args.page_group)
        return Settings(**kwargs)
    except (ValidationError, ValueError) as exc:
        raise _Usage(str(exc)) from exc


class _Usage(Exception):
    pass


def _common(parser: argparse.ArgumentParser, settings: bool = False) -> None:
    parser.add_argument("--state-dir", default=argparse.SUPPRESS,
                        help="state directory (database, sources, job artifacts, models). Default: $VT_STATE_DIR, "
                             "else <checkout>/.vt-state for an editable install or "
                             "~/.local/share/vision-to-tables for a wheel")
    parser.add_argument("--no-progress", action="store_true", default=argparse.SUPPRESS,
                        help="do not write JSON progress lines to stderr")
    if settings:
        parser.add_argument("--pipeline", choices=["direct", "hybrid"], default=None)
        parser.add_argument("--type", choices=["auto", "invoice", "menu", "recipe"], default=None,
                            help="document type (default auto)")
        parser.add_argument("--page-group", action="append", metavar="PAGES",
                            help="explicit group of source pages that form one document, e.g. 1,2 or 3-5; "
                                 "repeat for several groups. Ungrouped pages become one document each.")
        for name, helptext in (("max-bytes", "source byte limit"), ("max-pages", "pages read per source"),
                               ("max-pixels", "source pixel limit"), ("render-pixels", "rendered pixels per page"),
                               ("max-crops", "targeted crops per page"), ("max-tokens", "model tokens per call"),
                               ("retries", "bounded rereads per page")):
            parser.add_argument(f"--{name}", type=int, default=None, help=helptext)
        parser.add_argument("--lock-file", default=None, help="shared inference lock (default project-wide)")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="vision-tables", description="Local, evidence-backed document extraction.")
    parser.add_argument("--state-dir", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--no-progress", action="store_true", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)

    p = sub.add_parser("doctor", help="hardware, environment and model readiness")
    _common(p, True)
    p.set_defaults(handler=cmd_doctor)

    models = sub.add_parser("models", help="local model components").add_subparsers(
        dest="models_command", required=True, parser_class=_Parser)
    p = models.add_parser("prepare", help="provision pinned components (the only networked command)")
    _common(p, True)
    p.set_defaults(handler=cmd_prepare)

    p = sub.add_parser("extract", help="extract one file and publish JSON, CSV tables and review bundle")
    p.add_argument("file")
    p.add_argument("--output", required=True, help="export directory")
    _common(p, True)
    p.set_defaults(handler=cmd_extract)

    p = sub.add_parser("batch", help="recoverable batch over the files of a directory")
    p.add_argument("input_dir")
    p.add_argument("--output", required=True)
    p.add_argument("--retry-failed", action="store_true", help="rerun items that failed in an earlier run")
    p.add_argument("--retry-incomplete", action="store_true", help="rerun items that finished incomplete")
    _common(p, True)
    p.set_defaults(handler=cmd_batch)

    review = sub.add_parser("review", help="review bundles and corrections").add_subparsers(
        dest="review_command", required=True, parser_class=_Parser)
    p = review.add_parser("apply", help="apply a corrections file as a new version")
    p.add_argument("corrections_json")
    p.add_argument("--document", required=True)
    p.add_argument("--output", help="export directory for the new version (default <state>/exports)")
    _common(p)
    p.set_defaults(handler=cmd_apply)
    p = review.add_parser("bundle", help="write the review bundle of a version")
    p.add_argument("--document", required=True)
    p.add_argument("--version", type=int)
    p.add_argument("--output")
    _common(p)
    p.set_defaults(handler=cmd_bundle)

    p = sub.add_parser("export", help="export a canonical version")
    p.add_argument("--document", required=True)
    p.add_argument("--format", default="json", help="json, csv, review, sqlite, all, or a comma list")
    p.add_argument("--version", type=int, help="default: the latest version")
    p.add_argument("--output")
    _common(p)
    p.set_defaults(handler=cmd_export)

    p = sub.add_parser("evaluate", help="reproducible evaluation of a fixture manifest")
    p.add_argument("manifest")
    p.add_argument("--output", required=True)
    _common(p, True)
    p.set_defaults(handler=cmd_evaluate)

    jobs = sub.add_parser("jobs", help="inspect and recover jobs").add_subparsers(
        dest="jobs_command", required=True, parser_class=_Parser)
    p = jobs.add_parser("list")
    p.add_argument("--state", dest="job_state")
    _common(p)
    p.set_defaults(handler=cmd_jobs_list)
    p = jobs.add_parser("show")
    p.add_argument("job_id")
    _common(p)
    p.set_defaults(handler=cmd_jobs_show)
    p = jobs.add_parser("recover", help="finish interrupted persistence and pending exports")
    _common(p)
    p.set_defaults(handler=cmd_jobs_recover)

    docs = sub.add_parser("documents", help="inspect stored documents").add_subparsers(
        dest="documents_command", required=True, parser_class=_Parser)
    p = docs.add_parser("list")
    _common(p)
    p.set_defaults(handler=cmd_documents_list)
    p = docs.add_parser("show")
    p.add_argument("document_id")
    _common(p)
    p.set_defaults(handler=cmd_documents_show)
    return parser


# -- helpers --------------------------------------------------------------------------------------
def _progress(args):
    if getattr(args, "no_progress", False):
        return None

    def emit(event: dict) -> None:
        sys.stderr.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        sys.stderr.flush()
    return emit


def _state(args) -> Path:
    return Path(args.state_dir).resolve() if getattr(args, "state_dir", None) else default_state()


def _store(args) -> Store:
    return Store(_state(args))


def _out(args, store: Store) -> Path:
    return Path(args.output).resolve() if getattr(args, "output", None) else store.state_dir / "exports"


def _export_all(store: Store, targets: list[tuple[str, int, str]], formats=DEFAULT_FORMATS) -> tuple[list, bool]:
    results, ok = [], True
    for document_id, version, out in targets:
        try:
            store.register_export(document_id, version, out, tuple(formats))
            results.append({"state": "published", "document_id": document_id, "version": version,
                            **exports.publish(store, document_id, version, out, tuple(formats))})
        except (OSError, ProcessingError) as exc:
            ok = False
            results.append({"state": "pending", "document_id": document_id, "version": version,
                            "output_dir": out, "error": str(exc),
                            "next": "Run 'vision-tables jobs recover' to retry the pending export."})
    return results, ok


# -- commands -------------------------------------------------------------------------------------
def cmd_doctor(args):
    from . import models
    report = models.doctor(_settings(args))
    return {"command": "doctor", "status": "ready" if report["ready"] else "not_ready", **report}, (
        EXIT_OK if report["ready"] else EXIT_MISSING_RUNTIME)


def cmd_prepare(args):
    from . import models
    report = models.prepare(_settings(args))
    return {"command": "models prepare", "status": "ready" if report["ready"] else "not_ready", **report}, (
        EXIT_OK if report["ready"] else EXIT_MISSING_RUNTIME)


def cmd_extract(args):
    settings = _settings(args)
    source = Path(args.file)
    if not source.is_file():
        raise ProcessingError("invalid_input", f"{source} is not a readable file.")
    result = store_jobs.process_file(Store(settings.state_dir), source, Path(args.output), settings,
                                     progress=_progress(args))
    return {"command": "extract", "state_dir": str(settings.state_dir), **result}, result["exit_code"]


def cmd_batch(args):
    settings = _settings(args)
    result = store_jobs.run_batch(Store(settings.state_dir), Path(args.input_dir), Path(args.output), settings,
                                  progress=_progress(args), retry_failed=args.retry_failed,
                                  retry_incomplete=args.retry_incomplete)
    return {"command": "batch", "state_dir": str(settings.state_dir), **result}, result["exit_code"]


def cmd_apply(args):
    path = Path(args.corrections_json)
    try:
        with path.open("rb") as fp:
            raw = fp.read(CORRECTION_BYTES + 1)
        changes = CorrectionSet.model_validate(parse_object(raw, limit=CORRECTION_BYTES))
    except (OSError, ValueError) as exc:  # ValidationError is a ValueError
        raise ProcessingError("invalid_input", f"Corrections rejected before any change: {_brief(exc)}") from exc
    store = _store(args)
    result = store.apply_corrections(args.document, changes, extra_outputs=(str(_out(args, store)),))
    results, ok = _export_all(store, [(result.document_id, result.version, d) for d in result.output_dirs])
    doc = result.document
    return {"command": "review apply", "status": "replayed" if result.replayed else "applied",
            "document_id": result.document_id, "base_version": result.base_version, "version": result.version,
            "replayed": result.replayed, "validation": doc.validation, "approval": doc.approval,
            "review_required": doc.approval != "human_approved",
            "issue_codes": sorted({i.code for i in doc.issues}), "exports": results}, (
        EXIT_OK if ok else EXIT_EXPORT_PENDING)


def _brief(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'file'}: {e['msg']}" for e in exc.errors()[:8])
    return str(exc)


def _target(args) -> tuple[Store, str, int]:
    store = _store(args)
    version = args.version if getattr(args, "version", None) else None
    document = store.get_document(args.document, version)
    return store, args.document, document.version


def cmd_bundle(args):
    store, document_id, version = _target(args)
    results, ok = _export_all(store, [(document_id, version, str(_out(args, store)))], ("review",))
    return {"command": "review bundle", "document_id": document_id, "version": version, "exports": results}, (
        EXIT_OK if ok else EXIT_EXPORT_PENDING)


def cmd_export(args):
    formats = tuple(exports.FORMATS if args.format == "all" else
                    [f.strip() for f in args.format.split(",") if f.strip()])
    if not formats or set(formats) - set(exports.FORMATS):
        raise ProcessingError("invalid_format", f"--format must be one of {', '.join(exports.FORMATS)}, all, "
                                                "or a comma list.")
    store, document_id, version = _target(args)
    results, ok = _export_all(store, [(document_id, version, str(_out(args, store)))], formats)
    return {"command": "export", "document_id": document_id, "version": version, "formats": list(formats),
            "exports": results}, (EXIT_OK if ok else EXIT_EXPORT_PENDING)


def cmd_evaluate(args):
    from . import evaluate
    settings = _settings(args)
    report = evaluate.run(Path(args.manifest), Path(args.output), settings)
    cases = report.get("case_results", [])
    counts = Counter(c["status"] for c in cases if c.get("status"))
    blocked = sum(counts[k] for k in store_jobs.MISSING_RUNTIME)
    status, code = "completed", EXIT_OK
    if blocked:
        status, code = "blocked", EXIT_MISSING_RUNTIME
    elif counts["error"]:
        status, code = "failed", EXIT_INTERNAL
    return {"command": "evaluate", "status": status, "report": str(Path(args.output).resolve() / "evaluation.json"),
            "manifest_sha256": report.get("manifest_sha256"), "cases": len(cases),
            "case_status_counts": dict(counts), "blocked_cases": blocked,
            "cases_with_failures": sum(bool(c.get("failures")) for c in cases),
            "configuration": report.get("configuration")}, code


def cmd_jobs_list(args):
    rows = _store(args).list_jobs(args.job_state)
    keys = ("job_id", "kind", "state", "input_path", "output_dir", "artifact_root", "error_code", "batch_id",
            "created_at", "updated_at")
    return {"command": "jobs list", "jobs": [{k: r[k] for k in keys} for r in rows]}, EXIT_OK


def cmd_jobs_show(args):
    store = _store(args)
    row = store.get_job(args.job_id)
    if row is None:
        raise ProcessingError("invalid_input", f"No job {args.job_id} in this state directory.")
    row.pop("owner_token", None)
    root = store.artifact_root(args.job_id)
    try:
        pipeline_status = json.loads((root / "job.json").read_text())
    except (OSError, ValueError):
        pipeline_status = None
    return {"command": "jobs show", "job": row, "artifact_root": str(root), "pipeline_status": pipeline_status,
            "staged_result": (root / "documents.json").is_file()}, EXIT_OK


def cmd_jobs_recover(args):
    report = store_jobs.recover(_store(args), _progress(args))
    return {"command": "jobs recover", **report}, report["exit_code"]


def cmd_documents_list(args):
    return {"command": "documents list", "documents": _store(args).list_documents()}, EXIT_OK


def cmd_documents_show(args):
    store = _store(args)
    document = store.get_document(args.document_id)
    return {"command": "documents show", "document_id": args.document_id, "latest_version": document.version,
            "doc_type": document.doc_type, "validation": document.validation, "approval": document.approval,
            "versions": store.versions(args.document_id), "possible_duplicates": store.duplicates(args.document_id),
            "coverage": store_jobs._coverage(document)}, EXIT_OK


# -- entry point ----------------------------------------------------------------------------------
def _terminate(signum, frame):  # SIGTERM behaves like Ctrl-C so cancellation is recoverable
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, _terminate)
        signal.signal(signal.SIGINT, _terminate)
    try:
        payload, code = args.handler(args)
    except KeyboardInterrupt:
        payload, code = {"status": "cancelled", "error": {"code": "cancelled", "message": "Cancelled by signal.",
                                                         "next": "Rerun the command; recoverable state is kept."}}, EXIT_CANCELLED
    except _Usage as exc:
        payload, code = {"status": "usage_error", "error": {"code": "usage", "message": str(exc),
                                                           "next": "See --help."}}, EXIT_USAGE
    except ProcessingError as exc:
        payload, code = {"status": "failed", "error": error_payload(exc.code, str(exc))}, exit_code(exc.code)
    except Exception as exc:
        payload, code = {"status": "failed", "error": error_payload("internal_error",
                                                                    f"{type(exc).__name__}: {exc}")}, EXIT_INTERNAL
    payload["exit_code"] = code
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
