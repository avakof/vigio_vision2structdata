"""Deterministic French/English normalization of raw readings.

Every parser returns a ``Parsed``: a resolved value, or a null value with ``state="ambiguous"``,
candidate alternatives and a reason. Nothing here guesses: an unresolved reading stays null.
The raw text is never altered; callers keep it separately.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from .schema import ENUMS
from .units import DIMENSION, FRACTION_CHARS, MINOR_UNITS, currency_candidates, currency_in_amount, fold, split_currency, unit_code

MAX_DIGITS = 24
_SPACES = "     "


@dataclass
class Parsed:
    value: str | bool | None = None
    state: str = "readable"  # readable | ambiguous
    alternatives: list[str] = field(default_factory=list)
    reason: str | None = None
    number: Fraction | None = None          # exact numeric value for arithmetic
    bounds: tuple[Fraction, Fraction] | None = None  # a printed range, as (low, high)
    unit: str | None = None                 # unit printed inside a quantity ("250 g")
    currency: tuple[str, ...] = ()          # currency marker printed inside an amount


def unresolved(reason: str, alternatives: list[str] | None = None) -> Parsed:
    return Parsed(None, "ambiguous", list(alternatives or []), reason)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).replace(" ", " ")).strip()


def format_number(value: Fraction) -> str:
    """Decimal string when the expansion terminates, otherwise the reduced exact fraction 'n/d'."""
    denominator = value.denominator
    for p in (2, 5):
        while denominator % p == 0:
            denominator //= p
    if denominator != 1:
        return f"{value.numerator}/{value.denominator}"
    text = format(Decimal(value.numerator) / Decimal(value.denominator), "f") if value.denominator != 1 else str(value.numerator)
    return "0" if text in ("-0", "0.0") else text


def _decimal_text(d: Decimal) -> str:
    text = format(d, "f")
    return text[1:] if text.startswith("-") and d == 0 else text


def to_fraction(d: Decimal) -> Fraction:
    return Fraction(d)


# --- numbers ---------------------------------------------------------------------------------
def _sign(text: str) -> tuple[int, str]:
    t = text.strip().replace("−", "-").replace("–", "-")
    sign = 1
    if t.startswith("(") and t.endswith(")"):
        sign, t = -1, t[1:-1].strip()
    if t and t[0] in "+-":
        sign, t = (-sign if t[0] == "-" else sign), t[1:].strip()
    if t.endswith("-") and t[:-1].strip():
        sign, t = -sign, t[:-1].strip()
    return sign, t


def _decimal_candidates(core: str) -> list[Decimal] | str:
    """Candidate decimals for an unsigned digit string with grouping/decimal marks, or an error text."""
    if not re.fullmatch(r"[\d.,'     ]+", core) or not re.search(r"\d", core):
        return "not a number"
    spaced = bool(re.search(r"[     ]", core))
    parts_by_space = re.split(r"[     ]+", core)
    if spaced:
        head, *rest = parts_by_space
        tail = re.split(r"[.,]", rest[-1]) if rest else []
        groups = rest[:-1] + tail[:1]
        if not (re.fullmatch(r"\d{1,3}", head) and all(re.fullmatch(r"\d{3}", g) for g in groups)):
            return "irregular digit grouping"
        if len(tail) > 2 or (len(tail) == 2 and not tail[1].isdigit()):
            return "irregular digit grouping"
        core = "".join([head, *groups]) + ("," + tail[1] if len(tail) == 2 else "")
    if "'" in core:
        base, _, _ = core.partition(",")
        if not re.fullmatch(r"\d{1,3}(?:'\d{3})+(?:[.,]\d+)?", core):
            return "irregular digit grouping"
        core = core.replace("'", "")
    seps = [c for c in core if c in ".,"]
    kinds = set(seps)
    digits = re.sub(r"\D", "", core)
    if len(digits) > MAX_DIGITS:
        return "too many digits"
    if not seps:
        return [Decimal(core)]
    if len(kinds) == 2:
        last = max(core.rfind("."), core.rfind(","))
        group_mark = "," if core[last] == "." else "."
        whole, frac = core[:last], core[last + 1:]
        if (core[last] in whole or not frac.isdigit() or not re.fullmatch(rf"\d{{1,3}}(?:{re.escape(group_mark)}\d{{3}})*", whole)):
            return "irregular digit grouping"
        return [Decimal(whole.replace(group_mark, "") + "." + frac)]
    mark = seps[0]
    if len(seps) > 1:
        if not re.fullmatch(rf"\d{{1,3}}(?:{re.escape(mark)}\d{{3}})+", core):
            return "irregular digit grouping"
        return [Decimal(core.replace(mark, ""))]
    whole, frac = core.split(mark)
    if not frac:
        return "dangling decimal mark"
    if not frac.isdigit() or (whole and not whole.isdigit()):
        return "not a number"
    decimal = Decimal((whole or "0") + "." + frac)
    if len(frac) == 3 and 1 <= len(whole) <= 3 and not whole.startswith("0") and not spaced:
        return [decimal, Decimal(whole + frac)]  # '1,234': decimal mark or thousands separator
    return [decimal]


def parse_decimal(text: str, *, allow_negative: bool = True, trailing: str = "") -> Parsed:
    """'1 234,50' and '1,234.50' both give 1234.50; '1,234' stays ambiguous (1.234 or 1234)."""
    signed = _sign(text)
    sign, core = signed
    core = re.sub(trailing, "", core).strip() if trailing else core
    result = _decimal_candidates(core)
    if isinstance(result, str):
        return unresolved(result)
    if sign < 0 and not allow_negative:
        return unresolved("negative value not allowed here")
    values = [_decimal_text(-d if sign < 0 else d) for d in result]
    if len(values) > 1:
        return unresolved("single separator followed by three digits: decimal mark or thousands separator", values)
    d = Decimal(values[0])
    return Parsed(values[0], number=to_fraction(d))


_FRACTION_RE = re.compile(r"^(?:(\d+)\s+)?(\d+)\s*[/⁄]\s*(\d+)$")


def _expand_fractions(text: str) -> str:
    for char, repl in FRACTION_CHARS.items():
        text = text.replace(char, repl)
    return re.sub(r"(\d)\s+(\d+/\d+)", r"\1 \2", re.sub(r"\s+", " ", text)).strip()


def parse_number(text: str) -> Parsed:
    """Decimal, vulgar/ASCII fraction or mixed number ('1½', '1 1/2', '⅓' -> exact '1/3')."""
    t = _expand_fractions(text.strip())
    match = _FRACTION_RE.match(t)
    if match:
        whole, num, den = match.groups()
        if int(den) == 0:
            return unresolved("zero denominator")
        value = Fraction(int(whole or 0)) + Fraction(int(num), int(den))
        return Parsed(format_number(value), number=value)
    return parse_decimal(t)


_RANGE_SEP = r"(?:-|–|—|\bto\b|\bà\b|\ba\b|\bou\b|\bor\b)"
IMPRECISE = tuple(fold(p) for p in (
    "to taste", "a pinch", "pinch", "a dash", "dash", "a handful", "handful", "as needed", "as required", "a little",
    "a bit", "some", "au goût", "à votre goût", "selon le goût", "selon goût", "une pincée", "pincée", "un peu",
    "qs", "q.s.", "quantité suffisante", "en quantité suffisante", "une poignée", "poignée", "un filet", "filet",
    "quelques", "à volonté"))


def _imprecise(text: str) -> bool:
    key = fold(text)
    return any(key == p or key.startswith(p + " ") for p in IMPRECISE)


def parse_quantity(text: str) -> Parsed:
    """A number, a range (value stays null; bounds returned), or an imprecise phrase (value stays null)."""
    t = _expand_fractions(text.strip())
    if _imprecise(t):
        return Parsed(None, reason="imprecise quantity kept as written; no numeric value")
    number = r"\d[\d.,'   ]*(?: \d+/\d+)?|\d+/\d+"
    unit_tail = r"(?:\s*([^\d\s][^\d]*))?"
    match = re.fullmatch(rf"({number})\s*{_RANGE_SEP}\s*({number}){unit_tail}", t, re.IGNORECASE)
    if match:
        lo, hi = parse_number(match[1]), parse_number(match[2])
        if lo.state != "readable" or hi.state != "readable":
            return unresolved("range bound is ambiguous", [a for p in (lo, hi) for a in p.alternatives])
        if lo.number > hi.number:
            return unresolved("range bounds are reversed", [lo.value, hi.value])
        unit = unit_code(match[3]) if match[3] else None
        return Parsed(None, reason="range: see quantity_min and quantity_max", bounds=(lo.number, hi.number), unit=unit)
    match = re.fullmatch(rf"({number}){unit_tail}", t)
    if match and match[2]:
        code = unit_code(match[2])
        if code:
            parsed = parse_number(match[1])
            parsed.unit = code
            return parsed
        return unresolved("quantity carries unrecognized trailing text")
    return parse_number(t)


# --- money, rates, counts -------------------------------------------------------------------------
def parse_money(text: str) -> Parsed:
    markers, rest = split_currency(text)
    rest = re.sub(r"(?i)(?:^|\s)(?:HT|TTC|TVA|net|excl\.?|incl\.?)(?=\s|$)", " ", rest).strip()
    parsed = parse_decimal(rest)
    parsed.currency = markers
    return parsed


def parse_percent(text: str) -> Parsed:
    parsed = parse_decimal(text, allow_negative=False, trailing=r"\s*%$")
    if parsed.state == "readable" and not (0 <= parsed.number <= 100):
        return unresolved("rate outside 0..100")
    return parsed


def parse_count(text: str) -> Parsed:
    match = re.fullmatch(r"(?i)\s*(?:x\s*)?(\d{1,6})(?:\s*(?:x|pcs?|u|pi[eè]ces?|units?))?\s*", text)
    if not match or int(match[1]) == 0:
        return unresolved("not a positive whole number")
    return Parsed(str(int(match[1])), number=Fraction(int(match[1])))


# --- dates -----------------------------------------------------------------------------------------
_MONTHS = {}
for _i, _names in enumerate(("janvier janv jan january", "fevrier fevr fev feb february", "mars mar march", "avril avr apr april",
                             "mai may", "juin jun june", "juillet juil jul july", "aout aou aug august",
                             "septembre sept sep", "octobre oct", "novembre nov", "decembre dec"), 1):
    _MONTHS.update({n: _i for n in _names.split()})
_WEEKDAYS = "lundi mardi mercredi jeudi vendredi samedi dimanche lun mar mer jeu ven sam dim monday tuesday wednesday thursday friday saturday sunday mon tue wed thu fri sat sun".split()


def _valid(y: int, m: int, d: int) -> str | None:
    if 1 <= m <= 12 and 1 <= d <= calendar.monthrange(y, m)[1] and 1 <= y <= 9999:
        return date(y, m, d).isoformat()
    return None


def date_order_hint(raw: str) -> str | None:
    """'dmy' / 'mdy' when a numeric date forces one reading (a component above 12), else None."""
    match = re.fullmatch(r"\s*(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2}|\d{4})\s*", raw)
    if not match:
        return None
    a, b = int(match[1]), int(match[2])
    if a > 12 >= b >= 1:
        return "dmy"
    if b > 12 >= a >= 1:
        return "mdy"
    return None


def parse_date(text: str, *, order: str | None = None) -> Parsed:
    """Dates to ISO. '03/04/2026' without a disambiguating fact keeps both readings and no value."""
    t = text.strip()
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", t) or re.fullmatch(r"(\d{4})(\d{2})(\d{2})", t)
    if match:
        iso = _valid(int(match[1]), int(match[2]), int(match[3]))
        return Parsed(iso) if iso else unresolved("not a calendar date")
    match = re.fullmatch(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2}|\d{4})", t)
    if match:
        a, b, ys = int(match[1]), int(match[2]), match[3]
        years = [int(ys)] if len(ys) == 4 else [2000 + int(ys)]
        readings = {"dmy": _valid(years[0], b, a), "mdy": _valid(years[0], a, b)}
        found = [k for k, v in readings.items() if v]
        if not found:
            return unresolved("not a calendar date")
        distinct = sorted({readings[k] for k in found}, key=lambda iso: iso)
        if len(ys) == 2:
            return unresolved("two-digit year: century not printed", [readings[k] for k in found if readings[k]])
        if len(distinct) == 1:
            return Parsed(distinct[0])
        if order in readings and readings[order]:
            return Parsed(readings[order], reason=f"day/month order taken from a same-document date ({order})")
        return unresolved("day/month order not printed", [readings["dmy"], readings["mdy"]])
    folded = fold(t)
    folded = re.sub(r"\b(?:" + "|".join(_WEEKDAYS) + r")\b", " ", folded)
    folded = re.sub(r"\b(\d{1,2})(?:er|st|nd|rd|th|e)\b", r"\1", folded)
    folded = re.sub(r"\b(?:of|de|d|le|the)\b", " ", folded)
    folded = re.sub(r"\s+", " ", folded).strip()
    match = re.fullmatch(r"(\d{1,2}) ([a-z]+) (\d{4}|\d{2})", folded) or re.fullmatch(r"([a-z]+) (\d{1,2}) (\d{4}|\d{2})", folded)
    if match:
        parts = match.groups()
        if parts[0].isdigit():
            day, month_name, year = parts
        else:
            month_name, day, year = parts
        month = _MONTHS.get(month_name)
        if month is None:
            return unresolved("unrecognized month name")
        if len(year) == 2:
            return unresolved("two-digit year: century not printed", [d for d in [_valid(2000 + int(year), month, int(day))] if d])
        iso = _valid(int(year), month, int(day))
        return Parsed(iso) if iso else unresolved("not a calendar date")
    if re.fullmatch(r"[a-z]+ \d{4}", folded) and folded.split()[0] in _MONTHS:
        return unresolved("month and year only; no day printed")
    return unresolved("unrecognized date format")


# --- currency, units, enums, flags, time, temperature ---------------------------------------------
def parse_currency(text: str) -> Parsed:
    found = currency_candidates(text.strip()) or currency_in_amount(text)
    if len(found) == 1:
        return Parsed(found[0])
    if found:
        return unresolved("symbol does not identify one currency", list(found))
    return unresolved("unrecognized currency")


def parse_unit(text: str) -> Parsed:
    code = unit_code(text)
    if code is None:
        return unresolved("unrecognized unit; no conversion applied")
    reason = "unit has no fixed definition; never converted" if DIMENSION[code] == "culinary" else None
    return Parsed(code, reason=reason)


def parse_price_basis(text: str) -> Parsed:
    key = re.sub(r"^(?:per|par|le|la|l|au|aux|a|the|/)\s*", "", fold(text).replace("/", " / ").strip()).strip(" /")
    key = re.sub(r"^(?:per|par|le|la|l|au|a)\s+", "", key)
    if key in ("each", "unite", "unit", "piece", "ea", "u", "unitaire"):
        return Parsed("piece")
    code = unit_code(key)
    return Parsed(code) if code else unresolved("unrecognized price basis")


def _word_match(key: str, words: str) -> bool:
    return any(re.search(rf"(?:^| ){re.escape(w)}(?: |$)", key) for w in words.split("|"))


def parse_enum(name: str, text: str) -> Parsed:
    key = fold(text)
    if name == "document_kind":
        order = (("credit_note", "credit note|credit memo|avoir|note de credit"), ("delivery_note", "delivery note|delivery slip|packing slip|bon de livraison|bordereau de livraison"),
                 ("quote", "quote|quotation|estimate|devis"), ("receipt", "receipt|recu|ticket de caisse|ticket"),
                 ("invoice", "invoice|facture"), ("unknown", "unknown|inconnu"))
    elif name == "yield_type":
        order = (("portions", "portions|portion|servings|serving|serves|personnes|personne|pers|convives"),
                 ("mass", "mass|weight|poids|masse"), ("volume", "volume"), ("count", "pieces|piece|count|items|unites|pcs|nombre"))
    else:
        raise ValueError(name)
    hits = [code for code, words in order if _word_match(key, words)]
    if name == "yield_type" and len(hits) > 1:
        return unresolved("several recognized values", hits)
    if hits:  # document_kind: the list is in precedence order ('facture d'avoir' is a credit note)
        return Parsed(hits[0])
    return unresolved("no recognized value")


_TRUE = "true|yes|oui|vrai|optional|optionnel|optionnelle|facultatif|facultative|crossed out|struck|strikethrough|raye|rayee|barre|barree|x|✓"
_FALSE = "false|no|non|faux|required|obligatoire"


def parse_flag(text: str) -> Parsed:
    key = fold(text) or text.strip()
    if _word_match(key, _TRUE) and not _word_match(key, _FALSE):
        return Parsed(True)
    if _word_match(key, _FALSE) and not _word_match(key, _TRUE):
        return Parsed(False)
    return unresolved("cannot read a yes/no flag from this text")


_DUR_UNITS = {"h": 60, "hr": 60, "hrs": 60, "hour": 60, "hours": 60, "heure": 60, "heures": 60, "min": 1, "mins": 1,
              "minute": 1, "minutes": 1, "mn": 1, "s": Fraction(1, 60), "sec": Fraction(1, 60), "secs": Fraction(1, 60),
              "seconde": Fraction(1, 60), "secondes": Fraction(1, 60)}


def parse_duration(text: str) -> Parsed:
    t = _expand_fractions(text.casefold())
    t = unicodedata.normalize("NFKD", t).replace("'", " min ").replace("\u2032", " min ")
    t = "".join(c for c in t if not unicodedata.combining(c))
    number = r"\d+(?:[.,]\d+)?(?: \d+/\d+)?|\d+/\d+"
    found = re.findall(rf"({number}) ?(?:-|–|—|to|a|ou|or) ?({number})", t)
    if found:
        return unresolved("duration range: no single value", [x.replace(",", ".") for x in found[0]])
    total, last = Fraction(0), None
    token = re.compile(rf"({number})\s*([a-z]+)?")
    rest = t
    for m in token.finditer(t):
        unit = m[2]
        if unit is not None and unit not in _DUR_UNITS:
            return unresolved("unrecognized duration wording")
        value = parse_number(m[1].replace(",", "."))
        if value.number is None:
            return unresolved("unreadable number in duration")
        factor = _DUR_UNITS[unit] if unit else 1
        total += value.number * factor
        last = unit
    rest = token.sub(" ", t)
    if last is None and not token.search(t) or re.sub(r"\b(?:et|and)\b|[\s,.]", "", rest):
        return unresolved("unrecognized duration wording")
    return Parsed(format_number(total), number=total)


def parse_temperature(text: str, scale: str) -> Parsed:
    t = text.strip()
    match = re.fullmatch(r"(?i)(?:env\.?|about|approx\.?|~)?\s*(-?\d{1,4}(?:[.,]\d+)?)\s*(°\s*([CF])?|degrees?\s*([CF])?|degrés?\s*([CF])?|([CF]))?(?![A-Za-z])\.?", t)
    if not match:
        return unresolved("unrecognized temperature")
    printed = (match[3] or match[4] or match[5] or match[6] or "").upper()
    number = match[1].replace(",", ".")
    if not printed:
        return unresolved("temperature scale not printed", [f"{number} C", f"{number} F"])
    if printed != scale:
        return unresolved(f"scale printed as {printed}, field expects {scale}", [f"{number} {printed}"])
    return Parsed(_decimal_text(Decimal(number)), number=Fraction(Decimal(number)))


def normalize(lexical: str, raw: str, *, order: str | None = None) -> Parsed:
    """Dispatch on the vocabulary's lexical type. ``ref`` is resolved by the assembler."""
    if lexical == "text":
        text = clean_text(raw)
        return Parsed(text) if text else unresolved("empty text")
    if lexical == "money":
        return parse_money(raw)
    if lexical == "number":
        parsed = parse_number(raw)
        if parsed.state != "readable":  # '2 cartons', '75 cl': keep the number, report the printed unit
            tail = parse_quantity(raw)
            if tail.state == "readable" and tail.value is not None and tail.unit:
                return tail
        return parsed
    if lexical == "quantity":
        return parse_quantity(raw)
    if lexical == "percent":
        return parse_percent(raw)
    if lexical == "count":
        return parse_count(raw)
    if lexical == "date":
        return parse_date(raw, order=order)
    if lexical == "currency":
        return parse_currency(raw)
    if lexical == "flag":
        return parse_flag(raw)
    if lexical == "unit":
        return parse_unit(raw)
    if lexical == "price_basis":
        return parse_price_basis(raw)
    if lexical == "duration":
        return parse_duration(raw)
    if lexical == "temp_c":
        return parse_temperature(raw, "C")
    if lexical == "temp_f":
        return parse_temperature(raw, "F")
    if lexical.startswith("enum:"):
        return parse_enum(lexical[5:], raw)
    raise ValueError(f"no normalizer for {lexical}")


def minor_exponent(currency: str | None) -> int:
    return MINOR_UNITS.get(currency or "", 2)
