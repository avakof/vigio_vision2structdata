"""Derived observations and domain checks, recomputed from effective observations only.

``recompute`` is the single path used by assembly, revalidation and correction, so a fresh assembly and
a revalidated document agree. Calculated observations never replace printed ones, and no check changes an
observed value. Calculation policy (``CALC_VERSION``): exact rational arithmetic; money rounded half away
from zero to the currency's minor unit (2 decimals unless the currency is known otherwise) once per
calculated line, totals summed from printed line totals; the alternative of rounding once at the end is
reported (info) rather than hidden.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from ..contracts import Derivation, Entity, Evidence, Issue, Observation, Region
from . import catalog
from .normalize import format_number, minor_exponent, normalize, parse_quantity
from .schema import CRITICAL_FIELDS, EXPECTED_ON_ROOT, FIELD_TYPES, ROOT_KIND
from .units import DIMENSION, convert, currency_in_amount, fold

CALC_VERSION = "calc.v1"
_MONEY_FIELDS = {k: [f for f, t in v.items() if t == "money"] for k, v in FIELD_TYPES.items()}


def _fraction(value) -> Fraction | None:
    if not isinstance(value, str):
        return None
    try:
        return Fraction(value) if "/" in value else Fraction(Decimal(value))
    except (ValueError, ArithmeticError):
        return None


def money_text(value: Fraction, exponent: int) -> str:
    quantum = Decimal(1).scaleb(-exponent)
    text = format((Decimal(value.numerator) / Decimal(value.denominator)).quantize(quantum, rounding=ROUND_HALF_UP), "f")
    return text[1:] if text.startswith("-") and Decimal(text) == 0 else text


def _rounded(value: Fraction, exponent: int) -> Fraction:
    return Fraction(Decimal(money_text(value, exponent)))


class View:
    """Index over a document's entities and effective observations."""

    def __init__(self, entities: list[Entity], observations: list[Observation]):
        self.entities = {e.id: e for e in entities}
        self.order = [e.id for e in entities]
        self.by = {(o.entity_id, o.field): o for o in observations}
        self.obs_of: dict[str, list[Observation]] = {}
        for o in observations:
            self.obs_of.setdefault(o.entity_id, []).append(o)
        self.children_of: dict[str | None, list[Entity]] = {}
        for e in entities:
            self.children_of.setdefault(e.parent_id, []).append(e)

    def obs(self, eid: str, field: str) -> Observation | None:
        return self.by.get((eid, field))

    def value(self, eid: str, field: str):
        o = self.by.get((eid, field))
        return o.value if o is not None and o.state == "readable" else None

    def number(self, eid: str, field: str) -> Fraction | None:
        return _fraction(self.value(eid, field))

    def kids(self, eid: str, kind: str) -> list[Entity]:
        return [c for c in self.children_of.get(eid, ()) if c.kind == kind]


def _text(values) -> str:
    return ", ".join(str(v) for v in values)


def recompute(doc_type: str, entities: list[Entity], observations: list[Observation], regions: list[Region],
              incomplete_pages: set[int]) -> tuple[list[Observation], list[Issue]]:
    base = [o for o in observations if o.origin != "calculated"]
    calc: list[Observation] = []
    issues: list[Issue] = []
    ents = {e.id: e for e in entities}

    def view() -> View:
        return View(entities, base + calc)

    def add(entity: Entity, field: str, value: str, calculation: str, inputs: list[str]):
        calc.append(Observation(id=f"{entity.id}.{field}", entity_id=entity.id, field=field, raw=None, value=value,
                                state="readable", origin="calculated",
                                derivation=Derivation(calculation=f"{CALC_VERSION}: {calculation}", inputs=list(dict.fromkeys(inputs)))))

    def note(code: str, message: str, entity: Entity | None = None, observations_=(), severity=None, page=None):
        issues.append(catalog.make(code, message, page=page if page is not None else (entity.page if entity else None),
                                   observations=observations_, severity=severity))

    # ---- readings: unresolved values and evidence --------------------------------------------------
    v = view()
    region_by_id = {r.id: r for r in regions}
    for eid in v.order:
        entity = ents[eid]
        critical = CRITICAL_FIELDS.get(entity.kind, frozenset())
        for obs in v.obs_of.get(eid, ()):
            crit = obs.field in critical
            if obs.state in ("ambiguous", "unreadable"):
                severity = "review" if (crit or obs.origin == "observed") else "info"
                code = "ambiguous_reading" if obs.state == "ambiguous" else "unreadable_field"
                detail = obs.reason or ("candidates: " + _text(obs.alternatives))
                note(code, f"{entity.kind}.{obs.field}: {detail}", entity, [obs.id], severity)
            elif obs.state == "not_present" and entity.page in incomplete_pages:
                note("absence_unverified", f"{entity.kind}.{obs.field} reported absent on an incomplete page", entity, [obs.id])
            if obs.origin != "observed" or obs.state != "readable" or FIELD_TYPES[entity.kind][obs.field] == "ref":
                continue  # a ref is a record id chosen by the reader, not printed text
            severity = "review" if crit else "info"
            quote = obs.evidence[0].quote if obs.evidence else ""
            region = region_by_id.get(obs.evidence[0].region_id) if obs.evidence and obs.evidence[0].region_id else None
            raw_key = fold(obs.raw or "")
            if not quote.strip() and region is None:
                note("ungrounded_reading", f"{entity.kind}.{obs.field}: no supporting text or region", entity, [obs.id], severity)
            elif quote.strip() and raw_key and raw_key not in fold(quote):
                note("evidence_mismatch", f"{entity.kind}.{obs.field}: evidence does not contain the raw reading", entity, [obs.id], severity)
            if region is not None and region.text.strip() and raw_key and raw_key not in fold(region.text) and (
                    not quote.strip() or fold(quote) not in fold(region.text)):
                note("evidence_not_in_region", f"{entity.kind}.{obs.field}: cited region text does not support the reading", entity, [obs.id], severity)

    # ---- structure ---------------------------------------------------------------------------------
    root_kind = ROOT_KIND[doc_type]
    roots = [e for e in entities if e.kind == root_kind] if doc_type != "unknown" else []
    if doc_type != "unknown" and entities:
        if not roots:
            note("missing_root", f"no {root_kind} record was read on the processed pages")
        elif len(roots) > 1:
            note("multiple_roots", f"{len(roots)} {root_kind} records were read")
    for root in roots:
        for name in EXPECTED_ON_ROOT.get(doc_type, ()):
            if v.obs(root.id, name) is None:
                note("critical_missing", f"{root_kind}.{name} was not read", root)

    # ---- currency helpers ---------------------------------------------------------------------------
    def currency_of(entity: Entity) -> str | None:
        cursor = entity
        while cursor is not None:
            code = v.value(cursor.id, "currency") if "currency" in FIELD_TYPES[cursor.kind] else None
            if code:
                return code
            cursor = ents.get(cursor.parent_id) if cursor.parent_id else None
        return None

    for eid in v.order:
        entity = ents[eid]
        code = currency_of(entity)
        for field in _MONEY_FIELDS[entity.kind]:
            obs = v.obs(eid, field)
            if obs is None or obs.origin != "observed" or not obs.raw:
                continue
            marker = currency_in_amount(obs.raw)
            if code and marker and code not in marker:
                note("currency_conflict", f"{entity.kind}.{field} prints {_text(marker)} but the currency is {code}", entity, [obs.id])

    # ---- embedded units and ranges (observed text only) -----------------------------------------------
    unit_field = {("ingredient", "quantity"): "unit", ("substitution", "quantity"): "unit",
                  ("line_item", "quantity"): "purchase_unit", ("line_item", "pack_unit_quantity"): "pack_unit",
                  ("recipe", "yield_quantity"): "yield_unit",
                  ("subrecipe", "yield_quantity"): "yield_unit"}
    for eid in v.order:
        entity = ents[eid]
        for (kind, field), ufield in unit_field.items():
            obs = v.obs(eid, field)
            if entity.kind != kind or obs is None or obs.origin != "observed" or not obs.raw or obs.state == "unreadable":
                continue
            parsed = parse_quantity(obs.raw)
            if parsed.state == "readable" and parsed.unit:
                existing = v.obs(eid, ufield)
                if existing is None:
                    add(entity, ufield, parsed.unit, f"unit printed inside {field} text", [obs.id])
                elif existing.state == "readable" and existing.value != parsed.unit:
                    note("unit_conflict", f"{kind}.{field} prints unit {parsed.unit} but {ufield} reads {existing.value}", entity, [obs.id, existing.id])
        qobs = v.obs(eid, "quantity")
        if entity.kind == "ingredient" and qobs is not None and qobs.origin == "observed" and qobs.raw:
            parsed = parse_quantity(qobs.raw)
            if parsed.state == "readable" and parsed.bounds:
                for field, bound in zip(("quantity_min", "quantity_max"), parsed.bounds):
                    existing = v.obs(eid, field)
                    if existing is None:
                        add(entity, field, format_number(bound), "bound of the range printed in quantity text", [qobs.id])
                    elif existing.state == "readable" and _fraction(existing.value) != bound:
                        note("range_conflict", f"ingredient.{field} differs from the printed range", entity, [qobs.id, existing.id])
        lo, hi = v.number(eid, "quantity_min"), v.number(eid, "quantity_max")
        if entity.kind == "ingredient" and lo is not None and hi is not None and lo > hi:
            note("range_conflict", "ingredient.quantity_min exceeds quantity_max", entity,
                 [v.obs(eid, "quantity_min").id, v.obs(eid, "quantity_max").id])
    v = view()

    # ---- invoice -------------------------------------------------------------------------------------
    unrounded: dict[str, Fraction] = {}
    if doc_type == "invoice":
        _invoice(v, ents, add, note, calc, unrounded, currency_of, view)
    elif doc_type == "menu":
        _menu(v, ents, note)
    elif doc_type == "recipe":
        _recipe(v, ents, note, roots)
    _common_rows(v, ents, note)
    for eid in v.order:  # crossed-out / replacement readings
        entity = ents[eid]
        flag = v.obs(eid, "crossed_out")
        replacement = [f for f in ("replacement_price", "replacement_quantity") if v.obs(eid, f) is not None]
        if flag is not None and flag.state == "readable" and flag.value is True:
            note("crossed_out_reading", f"{entity.kind} has a struck reading", entity, [flag.id])
        elif replacement:
            note("replacement_without_crossout", f"{entity.kind}.{replacement[0]} without a crossed_out reading", entity,
                 [v.obs(eid, replacement[0]).id])

    # stable output order: observed observations in input order, calculated ones after their entity's
    rank = {eid: i for i, eid in enumerate(v.order)}
    ordered = sorted(enumerate(base + calc), key=lambda p: (rank[p[1].entity_id], p[1].origin == "calculated", p[0]))
    return [o for _, o in ordered], issues


def _invoice(v: View, ents, add, note, calc, unrounded, currency_of, view):
    exponent = lambda e: minor_exponent(currency_of(e))
    for eid in list(v.order):
        entity = ents[eid]
        if entity.kind != "line_item":
            continue
        ids = {f: v.obs(eid, f) for f in ("quantity", "pack_count", "pack_unit_quantity", "pack_unit", "purchase_unit",
                                          "unit_price", "price_basis", "discount", "discount_rate", "line_total")}
        quantity, pack, size = v.number(eid, "quantity"), v.number(eid, "pack_count"), v.number(eid, "pack_unit_quantity")
        pack_unit, purchase = v.value(eid, "pack_unit"), v.value(eid, "purchase_unit")
        pack_total = None
        have = [f for f in ("pack_count", "pack_unit_quantity", "pack_unit") if ids[f] is not None]
        if have:
            if None in (quantity, pack, size) or not pack_unit:
                if all(ids[f] is None or ids[f].state == "readable" for f in have + ["quantity"]):
                    missing = [f for f, x in (("quantity", quantity), ("pack_count", pack), ("pack_unit_quantity", size),
                                              ("pack_unit", pack_unit)) if not x]
                    note("pack_definition_incomplete", "no pack total: missing " + _text(missing), entity)
            elif purchase and DIMENSION.get(purchase) in ("mass", "volume"):
                note("pack_purchase_unit_conflict", f"purchase unit {purchase} cannot be a container of packs", entity,
                     [ids["purchase_unit"].id, ids["pack_unit"].id])
            else:
                pack_total = quantity * pack * size
                add(entity, "pack_total_quantity", format_number(pack_total),
                    f"quantity*pack_count*pack_unit_quantity, expressed in pack_unit {pack_unit}",
                    [ids[f].id for f in ("quantity", "pack_count", "pack_unit_quantity", "pack_unit")])
        price, total = v.number(eid, "unit_price"), v.number(eid, "line_total")
        if price is None or quantity is None:
            continue
        basis = v.value(eid, "price_basis")
        if ids["price_basis"] is not None and basis is None:
            continue
        multiplier, used = None, [ids["quantity"].id]
        if basis is None or basis == purchase or (basis == "piece" and purchase in (None, "piece")):
            multiplier = quantity
        elif purchase and convert(quantity, purchase, basis) is not None:
            multiplier = convert(quantity, purchase, basis)
            used.append(ids["purchase_unit"].id)
        elif pack_total is not None and pack_unit and convert(pack_total, pack_unit, basis) is not None:
            multiplier = convert(pack_total, pack_unit, basis)
            used += [f"{eid}.pack_total_quantity", ids["pack_unit"].id]
        if multiplier is None:
            note("line_check_indeterminate", f"price basis {basis} is not comparable with the quantity unit", entity, [ids["price_basis"].id])
            continue
        gross = price * multiplier
        used += [ids["unit_price"].id] + ([ids["price_basis"].id] if ids["price_basis"] else [])
        net = gross
        amount, rate = v.number(eid, "discount"), v.number(eid, "discount_rate")
        exp = exponent(entity)
        if amount is not None:
            net = gross - abs(amount)
            used.append(ids["discount"].id)
            if rate is not None and _rounded(gross * rate / 100, exp) != _rounded(abs(amount), exp):
                note("line_discount_conflict", "discount amount differs from discount rate applied to the gross amount", entity,
                     [ids["discount"].id, ids["discount_rate"].id])
        elif rate is not None:
            net = gross - gross * rate / 100
            used.append(ids["discount_rate"].id)
        elif ids["discount"] is not None or ids["discount_rate"] is not None:
            continue
        unrounded[eid] = net
        add(entity, "calculated_line_total", money_text(net, exp),
            "round_half_up(unit_price*multiplier - discount, minor unit); tax excluded", used)
        if total is None:
            continue
        got, want = _rounded(net, exp), _rounded(total, exp)
        if got == want:
            continue
        tax = v.number(eid, "tax")
        rate_t = v.number(eid, "tax_rate")
        if tax is not None and _rounded(net + tax, exp) == want or rate_t is not None and _rounded(net * (1 + rate_t / 100), exp) == want:
            note("line_total_matches_with_tax", "printed line total equals the calculation plus tax; tax-inclusive pricing is not inferred",
                 entity, [f"{eid}.calculated_line_total", ids["line_total"].id])
        else:
            note("line_total_mismatch", f"printed line total {money_text(total, exp)} but calculated {money_text(net, exp)}", entity,
                 [f"{eid}.calculated_line_total", ids["line_total"].id])
    v = view()
    for entity in (e for e in v.entities.values() if e.kind == "tax"):
        base_, rate, amount = (v.number(entity.id, f) for f in ("basis", "rate", "amount"))
        if None not in (base_, rate, amount):
            exp = exponent(entity)
            if _rounded(base_ * rate / 100, exp) != _rounded(amount, exp):
                note("tax_amount_mismatch", f"basis*rate gives {money_text(base_ * rate / 100, exp)}, printed {money_text(amount, exp)}",
                     entity, [v.obs(entity.id, f).id for f in ("basis", "rate", "amount")])
    for root in [e for e in v.entities.values() if e.kind == "invoice"]:
        exp = exponent(root)
        issue_d, due_d = v.value(root.id, "issue_date"), v.value(root.id, "due_date")
        if issue_d and due_d and due_d < issue_d:
            note("due_before_issue", "due date precedes issue date", root, [v.obs(root.id, "issue_date").id, v.obs(root.id, "due_date").id])
        lines = v.kids(root.id, "line_item")
        line_vals = [(l, v.number(l.id, "line_total")) for l in lines]
        lines_sum = sum((x for _, x in line_vals), Fraction(0)) if lines and all(x is not None for _, x in line_vals) else None
        line_ids = [v.obs(l.id, "line_total").id for l, x in line_vals if x is not None]
        subtotal = v.number(root.id, "subtotal")
        if subtotal is not None and lines_sum is not None and _rounded(subtotal, exp) != _rounded(lines_sum, exp):
            note("subtotal_mismatch", f"printed subtotal {money_text(subtotal, exp)} but line totals sum to {money_text(lines_sum, exp)}",
                 root, [v.obs(root.id, "subtotal").id] + line_ids)

        def aggregate(total_field, kind, code, absolute=False):
            printed = v.number(root.id, total_field)
            rows = [(c, v.number(c.id, "amount")) for c in v.kids(root.id, kind)]
            summed = None
            if rows and all(x is not None for _, x in rows):
                summed = sum((abs(x) if absolute else x for _, x in rows), Fraction(0))
            ids_ = [v.obs(c.id, "amount").id for c, x in rows if x is not None]
            if printed is not None and summed is not None and _rounded(abs(printed) if absolute else printed, exp) != _rounded(summed, exp):
                note(code, f"printed {total_field} {money_text(printed, exp)} but {kind} lines sum to {money_text(summed, exp)}",
                     root, [v.obs(root.id, total_field).id] + ids_)
            if printed is not None:
                return (abs(printed) if absolute else printed), [v.obs(root.id, total_field).id]
            return (summed, ids_) if summed is not None else (None, [])

        tax, tax_ids = aggregate("tax_total", "tax", "tax_sum_mismatch")
        charge, charge_ids = aggregate("charge_total", "charge", "charge_sum_mismatch")
        discount, disc_ids = aggregate("discount_total", "discount", "discount_sum_mismatch", absolute=True)
        printed_total = v.number(root.id, "total")
        if subtotal is not None:
            basis_value, basis_ids = subtotal, [v.obs(root.id, "subtotal").id]
        else:
            basis_value, basis_ids = lines_sum, line_ids
        if basis_value is None:
            if printed_total is not None and (lines or v.obs(root.id, "subtotal") is not None):
                note("total_check_indeterminate", "line totals or subtotal are missing or unresolved", root, [v.obs(root.id, "total").id])
            elif printed_total is not None:
                note("total_check_indeterminate", "no subtotal or line items were read to compare with the total", root, [v.obs(root.id, "total").id])
            continue
        parts = {"base": basis_value, "tax": tax or Fraction(0), "charge": charge or Fraction(0), "discount": discount or Fraction(0)}
        calculated = parts["base"] + parts["tax"] + parts["charge"] - parts["discount"]
        names = ["subtotal" if subtotal is not None else "sum of line totals"]
        names += [n for n, x in (("tax", tax), ("charges", charge)) if x is not None] + (["minus discounts"] if discount is not None else [])
        add(root, "calculated_total", money_text(calculated, exp),
            "round_half_up(" + " + ".join(names) + ", minor unit); absent components omitted",
            basis_ids + tax_ids + charge_ids + disc_ids)
        if printed_total is None:
            continue
        printed_r = _rounded(printed_total, exp)
        total_id = v.obs(root.id, "total").id
        if printed_r == _rounded(calculated, exp):
            continue
        if tax is not None and printed_r == _rounded(calculated - tax, exp):
            note("total_consistent_without_tax", "printed total equals the components without adding tax; tax-inclusive pricing is not inferred",
                 root, [total_id, f"{root.id}.calculated_total"])
        elif subtotal is None and lines and all(l.id in unrounded for l in lines) and printed_r == _rounded(
                sum(unrounded[l.id] for l in lines) + parts["tax"] + parts["charge"] - parts["discount"], exp):
            note("rounding_policy_difference", "printed total matches rounding once at the end instead of per line", root,
                 [total_id, f"{root.id}.calculated_total"])
        else:
            note("total_mismatch", f"printed total {money_text(printed_total, exp)} but calculated {money_text(calculated, exp)}", root,
                 [total_id, f"{root.id}.calculated_total"])


def _menu(v: View, ents, note):
    for eid in v.order:
        entity = ents[eid]
        if entity.kind in ("menu_item", "variant", "supplement", "bundle"):
            price = v.obs(eid, "price")
            named = any(v.value(eid, f) for f in ("name", "size"))
            if price is not None and not named:
                note("detached_price", f"{entity.kind} has a price but no readable name", entity, [price.id])
        if entity.kind == "menu_item":
            variants = v.kids(eid, "variant")
            price = v.obs(eid, "price")
            if price is None and not variants:
                note("item_without_price", "no price was read for this item", entity)
            elif price is not None and any(v.obs(c.id, "price") is not None for c in variants):
                note("price_and_variants", "item has its own price and priced variants", entity, [price.id])
        if entity.kind == "bundle_choice":
            ref = v.obs(eid, "item_ref")
            if ref is not None and ref.state == "readable" and (ref.value not in ents or ents[ref.value].kind != "menu_item"):
                note("invalid_item_ref", "item_ref does not name a menu item of this document", entity, [ref.id])
        for plain, translated in (("name", "translated_name"), ("description", "translated_description")):
            a, b = v.obs(eid, plain), v.obs(eid, translated)
            if a is None or b is None or a.state != "readable" or b.state != "readable":
                continue
            if fold(a.value or "") == fold(b.value or ""):
                note("identical_translation", f"{translated} equals {plain}", entity, [b.id])
            elif a.language and b.language and a.language == b.language:
                note("translation_same_language", f"{plain} and {translated} are both {a.language}", entity, [a.id, b.id])
            elif not any(ev.region_id for ev in a.evidence + b.evidence):
                note("translation_without_layout_evidence", f"{translated} paired without region evidence", entity, [b.id])


def _recipe(v: View, ents, note, roots):
    for entity in (e for e in ents.values() if e.kind in ("recipe", "subrecipe")):
        if entity.kind == "recipe" and v.obs(entity.id, "yield_quantity") is None:
            note("recipe_yield_missing", "no yield was read", entity)
        yield_type, unit = v.value(entity.id, "yield_type"), v.value(entity.id, "yield_unit")
        expected = {"portions": ("count",), "count": ("count", "container"), "mass": ("mass",), "volume": ("volume",)}
        if yield_type and unit and DIMENSION.get(unit) not in expected[yield_type]:
            note("yield_type_conflict", f"yield type {yield_type} does not fit unit {unit}", entity,
                 [v.obs(entity.id, "yield_type").id, v.obs(entity.id, "yield_unit").id])


def _common_rows(v: View, ents, note):
    seen: dict[tuple, list[str]] = {}
    for eid in v.order:
        entity = ents[eid]
        fields = tuple(sorted((o.field, o.raw) for o in v.obs_of.get(eid, ()) if o.origin == "observed" and o.raw))
        if len(fields) >= 2:
            seen.setdefault((entity.kind, entity.parent_id, entity.page, fields), []).append(eid)
    for group in seen.values():
        if len(group) > 1:
            first = ents[group[0]]
            obs = v.obs_of.get(group[0], [])[:1]
            note("duplicate_row", f"{len(group)} identical {first.kind} rows were kept as distinct records", first, [o.id for o in obs])
