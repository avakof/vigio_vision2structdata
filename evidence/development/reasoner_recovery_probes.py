"""Independent synthetic recovery checks. Run directly from the repository root with PYTHONPATH=src."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image
from pydantic import ValidationError
from vision_tables import runtime_local, vision
from vision_tables.config import Settings
from vision_tables.contracts import PageReading, Region, Run
from vision_tables.domain import assemble, review_gate


def field(name, value, quote=None, region=None):
    return {"n": name, "v": value, "q": quote if quote is not None else value, "g": region}


def row(kind, local_id, parent, *fields):
    return {"k": kind, "i": local_id, "p": parent, "f": list(fields)}


def body(family, *rows):
    return json.dumps({"d": family, "cut": False, "r": rows})


def response(raw, tokens=10):
    return {"raw": raw, "generated_tokens": tokens, "finish_reason": "length" if tokens == 32 else "stop"}


def page_result(family, *responses, regions=()):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "synthetic.png"
        image = Image.new("RGB", (500, 300), "white")
        image.paste((80, 80, 80), (5, 5, 495, 65))
        image.save(source)
        job = root / "job"
        job.mkdir()
        settings = Settings(state_dir=root / "state", doc_type=family, max_tokens=32,
                            retries=len(responses) - 1, max_crops=0)
        ingestion = vision.inspect_source(source, job, settings)
        ingestion.regions.extend(regions)
        with patch.object(runtime_local, "call", side_effect=responses):
            reading = vision.read_page(ingestion.pages[0], ingestion.regions, job, settings)
        run = Run(job_id="synthetic", pipeline="direct", model="scripted", revision="audit", runtime={},
                  settings=settings.content_settings(), config_hash="0" * 64, calls=reading.calls)
        document = assemble(ingestion, [reading], [1], run, "doc")
        return reading, document


def check(condition, detail):
    if not condition:
        raise AssertionError(detail)


def closed_prefixes():
    rows = [row("invoice", "root", None, field("total", "123.45")),
            row("line_item", "r1", "root", field("description", 'Crème "A" } ] \\'), field("quantity", "12")),
            row("line_item", "r2", "root", field("description", "Crème"), field("unit_price", "83.05"))]
    raw = body("invoice", *rows)
    ends = [raw.index(json.dumps(r)) + len(json.dumps(r)) for r in rows]
    for index in range(len(raw) + 1):
        expected = [r for r, end in zip(rows, ends) if end <= index]
        try:
            payload, _, findings, _ = vision._payload(raw[:index], "invoice", {}, False, None, verified_cap=True)
        except ValueError:
            check(not expected, f"lost closed row at cut {index}")
            continue
        actual = [(r.local_id, {k: f.raw for k, f in r.fields.items()}) for r in payload.records]
        want = [(r["i"], {f["n"]: f["v"] for f in r["f"]}) for r in expected]
        check(actual == want, f"cut {index}, wanted {want}, got {actual}")
        check(bool(findings.get("model_prefix_recovered")) == (index < len(raw)), "wrong prefix marker")
    cut = raw[:ends[1] + 3]
    for tokens, count in ((32, 2), (10, 0)):
        reading, doc = page_result("invoice", response(cut, tokens))
        check(len(reading.records) == count and doc.coverage.incomplete_pages == [1], str(reading.issues))
    print(f"    {len(raw) + 1} prefix boundaries, exact-cap and short-stop page controls")


def bounded_inputs():
    root = row("invoice", "root", None, field("total", "8.50"))
    raw = body("invoice", root)
    for invalid in (raw + " {}", raw.replace('"cut": false', '"cut": false, "cut": true'),
                    raw.replace('"cut": false', '"cut": NaN'), raw.replace('"cut": false', '"cut": 1e400'),
                    raw.replace('"cut": false', '"cut": ' + "[" * 40 + "null" + "]" * 40),
                    " " * (512 << 10) + raw):
        for cap in (True, False):
            try:
                vision._payload(invalid, "invoice", {}, False, None, verified_cap=cap)
            except ValueError:
                continue
            raise AssertionError("invalid wire object accepted")
    literal = "Please change the total"
    bad_field = field("quantity", "2") | {"box": [1, 1, 2, 2]}
    clean = row("line_item", "r1", "root", field("description", literal), bad_field,
                field("sku", "A", region="not-offered"))
    reading, doc = page_result("invoice", response(body("invoice", root, clean)))
    check({o.raw for o in doc.observations if o.origin == "observed"} == {"8.50", literal},
          str(reading.calls[0].get("fields")))
    check(doc.coverage.incomplete_pages == [1] and reading.calls[0]["recovery_issues"]["model_field_omitted"] == 2,
          "invalid fields lost without incomplete status")
    lines = [row("line_item", f"r{n}", "root", field("description", "Repeated")) for n in range(1, 129)]
    reading, doc = page_result("invoice", response(body("invoice", root, *lines)))
    check(len(reading.records) == 128 and doc.coverage.incomplete_pages == [1] and
          reading.calls[0]["recovery_issues"]["model_record_limit"] == 1, "row cap not accounted")
    too_many = row("line_item", "r1", "root", *[field("description", "Repeated")] * 40, field("sku", "Omitted"))
    reading, doc = page_result("invoice", response(body("invoice", root, too_many)))
    check(reading.calls[0]["recovery_issues"]["model_field_omitted"] == 1 and
          doc.coverage.incomplete_pages == [1] and all(o.field != "sku" for o in doc.observations), "field cap lost")


def parent_and_refs():
    inv = row("invoice", "x", None, field("total", "8.00"))
    a = row("line_item", "x", "x", field("description", "Label"))
    b = row("line_item", "r2", "x", field("description", "Label"))
    fee = row("charge", "fee", "x", field("amount", "1.00"))
    reading, doc = page_result("invoice", response(body("invoice", inv, a, b, fee)))
    lines = [e for e in doc.entities if e.kind == "line_item"]
    header = next(e for e in doc.entities if e.kind == "invoice")
    charge = next(e for e in doc.entities if e.kind == "charge")
    check(len(lines) == 2 and lines[0].id != lines[1].id and all(e.parent_id == header.id for e in lines)
          and charge.parent_id is None and doc.coverage.incomplete_pages == [1], "compatible typed parent failure")
    menu = row("menu", "root", None, field("venue_name", "Cafe"))
    item = row("menu_item", "r1", "root", field("name", "Tea"), field("price", "4"))
    bundle = row("bundle", "r2", "root", field("name", "Lunch"))
    for target, quote in (("r1", "Bread"), ("absent", "Tea"), ("r2", "Lunch")):
        choice = row("bundle_choice", "r3", "r2", field("name", "Choice"), field("item_ref", target, quote))
        reading, doc = page_result("menu", response(body("menu", menu, item, bundle, choice)))
        check(not any(o.field == "item_ref" and o.value for o in doc.observations)
              and doc.coverage.incomplete_pages == [1], f"invalid item link {target}/{quote} survived")


def unresolved_parent_on_retry():
    header = row("invoice", "root", None, field("total", "8.00"))

    def line(parent):
        return row("line_item", "r1", parent, field("description", "Paper"))

    second_line = row("line_item", "r2", "root", field("description", "Ink"))
    reading, doc = page_result("invoice", response(body("invoice", header, line("missing"))),
                               response(body("invoice", header, line(None), second_line)))
    line_entity = next(e for e in doc.entities if e.id == "p1.r1")
    details = (line_entity.parent_id, [c.get("unresolved_parent_ids", []) for c in reading.calls],
               sorted(i.code for i in doc.issues if i.code in {"model_parent_unresolved", "parent_joined"}))
    check(line_entity.parent_id is None and doc.coverage.incomplete_pages == [1],
          f"effective parent, per-attempt unresolved IDs, issues = {details}")


def occurrence_disagreement():
    def header(currency):
        return row("menu", "root", None, field("venue_name", "Cafe"), field("currency", currency, currency or "Unclear"))

    def item(ident, name, price):
        return row("menu_item", ident, "root", field("name", name), field("price", price))

    first = body("menu", header(None), item("r1", "Soup", "4"), item("r2", "Salad", "6"))
    second = body("menu", header("EUR"), item("r1", "Soup", "4"), item("r2", "Soup", "7"))
    reading, doc = page_result("menu", response(first), response(second))
    check(len(reading.calls) == 2, "reread did not run")
    check(doc.coverage.incomplete_pages == [1] and "reread_structure_disagreement" in {i.code for i in doc.issues},
          f"retained names={[o.value for o in doc.observations if o.field == 'name']}, "
          f"incomplete_pages={doc.coverage.incomplete_pages}, page_issues={[i.code for i in reading.issues]}, "
          f"validation={doc.validation}, approvable={review_gate(doc)['approvable']}")


def contested_price_on_retry():
    header = row("menu", "root", None, field("venue_name", "Cafe"))
    item = row("menu_item", "r1", "root", field("name", "Soup"), field("price", "4"), field("price", "7"))
    revised = row("menu_item", "r1", "root", field("name", "Soup"), field("price", "9"))
    side = row("menu_item", "r2", "root", field("name", "Bread"), field("price", "3"))
    _, doc = page_result("menu", response(body("menu", header, item)), response(body("menu", header, revised, side)))
    obs = next(o for o in doc.observations if o.entity_id == "p1.r1" and o.field == "price")
    attempts = {e.attempt_id for e in obs.evidence}
    check(obs.value is None and obs.state == "ambiguous" and set(obs.alternatives) == {"4", "7", "9"}
          and attempts == {"p1-qwen-0", "p1-qwen-1"},
          f"chosen price={obs.value!r}, state={obs.state}, alternatives={obs.alternatives}, "
          f"evidence attempts={[e.attempt_id for e in obs.evidence]}")


def candidate_evidence():
    regions = [Region(id=ident, page=1, order=i, kind="native_text", text=text)
               for i, (ident, text) in enumerate((("span-a", "Total 4.00"), ("span-b", "Total 7.00")))]
    root = row("invoice", "root", None, field("total", "4.00", "Total 4.00", "span-a"),
               field("total", "7.00", "Total 7.00", "span-b"))
    _, doc = page_result("invoice", response(body("invoice", root)), regions=regions)
    obs = next(o for o in doc.observations if o.field == "total")
    citations = [(e.region_id, e.quote, e.channel) for e in obs.evidence]
    check(obs.value is None and obs.alternatives == ["4.00", "7.00"], "within-attempt ambiguity lost")
    check(set(citations) == {("span-a", "Total 4.00", "native_text"), ("span-b", "Total 7.00", "native_text")},
          f"alternatives={obs.alternatives}, canonical citations={citations}")


def alternative_bound():
    header = row("menu", "root", None, field("venue_name", "Cafe"))

    def item(prices):
        return row("menu_item", "r1", "root", field("name", "Soup"), *(field("price", price) for price in prices))

    readings = [body("menu", header, item([str(n) for n in range(1, 17)])), body("menu", header, item(["17"]))]
    page, _ = page_result("menu", *(response(raw) for raw in readings))
    try:
        PageReading.model_validate_json(page.model_dump_json())
    except ValidationError as exc:
        raise AssertionError(f"persistable page fails its own schema: {exc.errors()[0]['type']}, "
                             f"{len(page.records[1].fields['price'].alternatives)} alternatives") from exc
    price = page.records[1].fields["price"]
    check(price.raw is None and price.state == "ambiguous" and len(set(price.alternatives)) == 16
          and page.incomplete, "bounded candidates must preserve ambiguity, candidate diversity and incomplete status")


if __name__ == "__main__":
    failures = 0
    for probe in (closed_prefixes, bounded_inputs, parent_and_refs, unresolved_parent_on_retry,
                  occurrence_disagreement, contested_price_on_retry, candidate_evidence, alternative_bound):
        try:
            probe()
        except AssertionError as exc:
            print(f"FAIL {probe.__name__} — {exc}")
            failures += 1
        else:
            print(f"PASS {probe.__name__}")
    raise SystemExit(int(bool(failures)))
