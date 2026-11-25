from fractions import Fraction

import pytest

from vision_tables.domain.normalize import normalize
from vision_tables.domain.units import convert


def value(kind, raw, **kw):
    parsed = normalize(kind, raw, **kw)
    return parsed.value, parsed.state


@pytest.mark.parametrize("raw", ["1 234,50", "1,234.50", "1 234,50", "1.234,50", "1'234.50", "€ 1 234,50", "1 234,50 EUR"])
def test_grouping_and_decimal_marks_give_one_exact_amount(raw):
    assert value("money", raw) == ("1234.50", "readable")


def test_single_separator_with_three_digits_is_ambiguous_but_keeps_both_readings():
    parsed = normalize("money", "1,234")
    assert (parsed.value, parsed.state, parsed.alternatives) == (None, "ambiguous", ["1.234", "1234"])
    assert value("money", "0,335") == ("0.335", "readable")
    assert value("money", "1 234,567") == ("1234.567", "readable")


@pytest.mark.parametrize("raw,expected", [("(12,50)", "-12.50"), ("12.50-", "-12.50"), ("-8,50", "-8.50"), ("+ 1,50", "1.50"), ("−8.50", "-8.50")])
def test_sign_is_kept_as_printed(raw, expected):
    assert value("money", raw) == (expected, "readable")


@pytest.mark.parametrize("raw", ["abc", "1 23", "12,", "1.2.3", "12 kg", ""])
def test_unparseable_amounts_stay_null_with_a_reason(raw):
    parsed = normalize("money", raw)
    assert parsed.value is None and parsed.state == "ambiguous" and parsed.reason


def test_fractions_are_exact():
    assert value("number", "⅓") == ("1/3", "readable")
    assert value("number", "1½") == ("1.5", "readable")
    assert value("number", "1 1/3") == ("4/3", "readable")
    assert value("number", "0,25") == ("0.25", "readable")
    assert normalize("number", "1/0").state == "ambiguous"


def test_ranges_and_imprecise_quantities_have_no_point_value():
    parsed = normalize("quantity", "2–3")
    assert parsed.value is None and parsed.state == "readable" and parsed.bounds == (Fraction(2), Fraction(3))
    assert normalize("quantity", "1 à 2 kg").unit == "kg"
    assert normalize("quantity", "3-2").state == "ambiguous"
    for phrase in ("to taste", "au goût", "une pincée", "a pinch", "q.s."):
        assert normalize("quantity", phrase).value is None and normalize("quantity", phrase).state == "readable"
    assert (normalize("quantity", "250 g").value, normalize("quantity", "250 g").unit) == ("250", "g")


def test_dates_keep_unresolved_order_and_century():
    parsed = normalize("date", "03/04/2026")
    assert (parsed.value, parsed.alternatives) == (None, ["2026-04-03", "2026-03-04"])
    assert value("date", "13/04/2026") == ("2026-04-13", "readable")
    assert value("date", "04/13/2026") == ("2026-04-13", "readable")
    assert value("date", "03/03/2026") == ("2026-03-03", "readable")
    assert normalize("date", "03/04/26").value is None
    assert normalize("date", "31/04/2026").state == "ambiguous"
    assert normalize("date", "avril 2026").value is None
    forced = normalize("date", "03/04/2026", order="dmy")
    assert forced.value == "2026-04-03" and "same-document" in forced.reason


@pytest.mark.parametrize("raw", ["3 avril 2026", "April 3rd, 2026", "lundi 3 avril 2026", "3 Apr 2026", "2026-04-03", "Apr. 3 2026"])
def test_named_months_in_french_and_english(raw):
    assert value("date", raw) == ("2026-04-03", "readable")
    assert value("date", "1er mai 2026") == ("2026-05-01", "readable")


def test_currency_symbols_do_not_invent_codes():
    assert value("currency", "€") == ("EUR", "readable")
    assert value("currency", "eur") == ("EUR", "readable")
    dollar = normalize("currency", "$")
    assert dollar.value is None and dollar.alternatives == ["USD", "CAD", "AUD"]
    assert normalize("currency", "¥").value is None
    assert normalize("currency", "pesos").value is None


def test_units_french_english_and_no_cross_dimension_conversion():
    assert value("unit", "Kg.") == ("kg", "readable")
    assert value("unit", "cuillères à soupe") == ("tbsp", "readable")
    assert value("unit", "bouteilles") == ("bottle", "readable")
    assert normalize("unit", "tbsp").reason and "never converted" in normalize("unit", "tbsp").reason
    assert normalize("unit", "furlong").state == "ambiguous"
    assert convert(Fraction(2), "kg", "g") == 2000
    assert convert(Fraction(1), "l", "kg") is None        # volume to mass needs an ingredient-specific density
    assert convert(Fraction(1), "tbsp", "ml") is None      # spoon size is not defined
    assert convert(Fraction(1), "cup", "ml") is None


@pytest.mark.parametrize("kind,raw,expected", [("percent", "5,5 %", "5.5"), ("percent", "20%", "20"), ("count", "x6", "6"),
                                               ("price_basis", "/kg", "kg"), ("price_basis", "par carton", "carton"),
                                               ("price_basis", "l'unité", "piece"), ("flag", "barré", True), ("flag", "optionnel", True),
                                               ("flag", "no", False), ("enum:document_kind", "Facture d'avoir", "credit_note"),
                                               ("enum:document_kind", "FACTURE", "invoice"), ("enum:document_kind", "Devis", "quote"),
                                               ("enum:yield_type", "Pour 4 personnes", "portions"), ("text", "  Soupe   à l'oignon ", "Soupe à l'oignon")])
def test_other_lexical_types(kind, raw, expected):
    assert value(kind, raw) == (expected, "readable")
    assert normalize("percent", "120 %").state == "ambiguous"
    assert normalize("count", "0").state == "ambiguous"


@pytest.mark.parametrize("raw,expected", [("1h30", "90"), ("1 heure 15 minutes", "75"), ("45'", "45"), ("2 heures", "120"), ("35 min", "35"), ("1½ h", "90")])
def test_durations(raw, expected):
    assert value("duration", raw) == (expected, "readable")
    assert normalize("duration", "20-25 min").alternatives == ["20", "25"]
    assert normalize("duration", "toute la nuit").value is None


def test_temperature_scale_must_be_printed_and_match_the_field():
    assert value("temp_c", "180 °C") == ("180", "readable")
    assert value("temp_f", "350°F") == ("350", "readable")
    bare = normalize("temp_c", "180°")
    assert bare.value is None and bare.alternatives == ["180 C", "180 F"]
    assert normalize("temp_c", "350°F").value is None


def test_numbers_may_carry_their_printed_unit():
    parsed = normalize("number", "2 cartons")
    assert (parsed.value, parsed.unit, parsed.state) == ("2", "carton", "readable")
    assert normalize("number", "75 cl").unit == "cl" and normalize("number", "2 widgets").state == "ambiguous"
    assert normalize("quantity", "1 1/2 cups").value == "1.5"


def test_arbitrary_text_never_raises_and_never_yields_non_canonical_numbers():
    import random
    import re
    rng = random.Random(7)
    alphabet = list("0123456789 ,.'-+()/%$€£¥°:xX½⅓  abcdeghklmnopqrstu–àéè") + ["EUR", "kg", " cl", "à", "to", "min", "°C", "h"]
    types = ["text", "money", "number", "quantity", "percent", "count", "date", "currency", "flag", "unit", "price_basis",
             "duration", "temp_c", "temp_f", "enum:document_kind", "enum:yield_type"]
    for _ in range(4000):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
        for kind in types:
            parsed = normalize(kind, raw)
            assert parsed.state in ("readable", "ambiguous")
            if parsed.state == "ambiguous":
                assert parsed.value is None and parsed.reason
            if kind in ("money", "percent", "temp_c") and isinstance(parsed.value, str):
                assert re.fullmatch(r"-?\d+(\.\d+)?", parsed.value), (kind, raw, parsed.value)
            if kind in ("number", "duration") and isinstance(parsed.value, str):
                assert re.fullmatch(r"-?\d+(\.\d+)?|-?\d+/\d+", parsed.value), (raw, parsed.value)
            if kind == "date" and parsed.value:
                assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", parsed.value)


def test_pathological_inputs_are_bounded():
    import time
    started = time.monotonic()
    for raw in ("1" * 16000 + "x", "1 " * 4000 + "x", "1,1" * 5000, "(" * 16000):
        for kind in ("money", "number", "quantity", "date", "duration", "percent", "unit", "enum:document_kind"):
            assert normalize(kind, raw).state == "ambiguous" or kind in ("unit",)
    assert time.monotonic() - started < 2


@pytest.mark.parametrize("raw,expected,markers", [
    ("-$11.95", "-11.95", ("USD", "CAD", "AUD")), ("$-11.95", "-11.95", ("USD", "CAD", "AUD")),
    ("+$5", "5", ("USD", "CAD", "AUD")), ("$11.95", "11.95", ("USD", "CAD", "AUD")),
    ("($11.95)", "-11.95", ("USD", "CAD", "AUD")), ("- 11.95 $", "-11.95", ("USD", "CAD", "AUD")),
    ("−€3,00", "-3.00", ("EUR",)), ("€-11,95", "-11.95", ("EUR",)), ("-11,95 €", "-11.95", ("EUR",)),
    ("(11,95 €)", "-11.95", ("EUR",)), ("11,95 €-", "-11.95", ("EUR",)), ("11.95-$", "-11.95", ("USD", "CAD", "AUD")),
    ("-CA$ 11.95", "-11.95", ("CAD",)), ("US$-11.95", "-11.95", ("USD",)), ("-USD 1 234,50", "-1234.50", ("USD",)),
    ("-$1,234.50", "-1234.50", ("USD", "CAD", "AUD"))])
def test_sign_and_currency_marker_are_independent_in_any_order(raw, expected, markers):
    parsed = normalize("money", raw)
    assert (parsed.value, parsed.state, parsed.currency) == (expected, "readable", markers)


def test_currency_marker_never_resolves_the_currency_field_and_bad_signs_stay_unresolved():
    assert normalize("currency", "$").value is None
    for raw in ("--$1", "$", "-", "$-", "-$-"):
        assert normalize("money", raw).value is None
