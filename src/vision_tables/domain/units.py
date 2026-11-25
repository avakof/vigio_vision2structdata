"""French/English unit and currency vocabularies. Conversion is exact and only within one dimension.

Spoon, cup, pinch and bunch sizes are not fixed (metric vs US vs UK vs regional), so those units are
"culinary": they get a canonical code but never a conversion factor. Volume is never converted to mass.
"""
from __future__ import annotations

import re
import unicodedata
from fractions import Fraction

# canonical code -> (dimension, aliases separated by "|"); aliases are matched after fold().
_UNITS = {
    "mg": ("mass", "mg|milligram|milligrams|milligramme|milligrammes"),
    "g": ("mass", "g|gr|grs|gramme|grammes|gram|grams"),
    "kg": ("mass", "kg|kgs|kilo|kilos|kilogramme|kilogrammes|kilogram|kilograms"),
    "oz": ("mass", "oz|ounce|ounces|once|onces"),
    "lb": ("mass", "lb|lbs|pound|pounds"),
    # fl oz, pint and gallon differ between US and UK and are intentionally absent.
    "ml": ("volume", "ml|millilitre|millilitres|milliliter|milliliters"),
    "cl": ("volume", "cl|centilitre|centilitres|centiliter|centiliters"),
    "dl": ("volume", "dl|decilitre|decilitres|deciliter|deciliters"),
    "l": ("volume", "l|lt|litre|litres|liter|liters"),
    "tsp": ("culinary", "tsp|teaspoon|teaspoons|cuillere a cafe|cuilleres a cafe|cuillere cafe|c a c|c a cafe|cuil a cafe|cac"),
    "tbsp": ("culinary", "tbsp|tablespoon|tablespoons|cuillere a soupe|cuilleres a soupe|cuillere soupe|c a s|c a soupe|cuil a soupe|cas"),
    "cup": ("culinary", "cup|cups|tasse|tasses"),
    "pinch": ("culinary", "pinch|pinches|pincee|pincees"),
    "bunch": ("culinary", "bunch|bunches|bouquet|bouquets|botte|bottes"),
    "piece": ("count", "pc|pcs|piece|pieces|unit|units|unite|unites|u|ea|each"),
    "slice": ("count", "slice|slices|tranche|tranches"),
    "clove": ("count", "clove|cloves|gousse|gousses"),
    "sprig": ("count", "sprig|sprigs|brin|brins"),
    "leaf": ("count", "leaf|leaves|feuille|feuilles"),
    "portion": ("count", "portion|portions|serving|servings|personne|personnes|pers"),
    "carton": ("container", "carton|cartons|ctn"),
    "case": ("container", "case|cases|caisse|caisses"),
    "box": ("container", "box|boxes|boite|boites"),
    "bag": ("container", "bag|bags|sac|sacs"),
    "pack": ("container", "pack|packs|packet|packets|paquet|paquets|pk"),
    "bottle": ("container", "bottle|bottles|bouteille|bouteilles|btl|btle|bt"),
    "can": ("container", "can|cans|tin|tins"),
    "jar": ("container", "jar|jars|bocal|bocaux|pot|pots"),
    "tray": ("container", "tray|trays|barquette|barquettes"),
    "roll": ("container", "roll|rolls|rouleau|rouleaux"),
    "pallet": ("container", "pallet|pallets|palette|palettes"),
}
DIMENSION = {code: dim for code, (dim, _) in _UNITS.items()}
UNIT_CODES = tuple(_UNITS)
_FACTOR = {"mg": Fraction(1, 1000), "g": Fraction(1), "kg": Fraction(1000), "oz": Fraction(28349523125, 10**9),
           "lb": Fraction(45359237, 10**5), "ml": Fraction(1), "cl": Fraction(10), "dl": Fraction(100), "l": Fraction(1000)}
FRACTION_CHARS = {"¼": " 1/4", "½": " 1/2", "¾": " 3/4", "⅐": " 1/7", "⅑": " 1/9", "⅒": " 1/10", "⅓": " 1/3",
                  "⅔": " 2/3", "⅕": " 1/5", "⅖": " 2/5", "⅗": " 3/5", "⅘": " 4/5", "⅙": " 1/6", "⅚": " 5/6",
                  "⅛": " 1/8", "⅜": " 3/8", "⅝": " 5/8", "⅞": " 7/8"}


def fold(text: str) -> str:
    """Lowercase, strip accents, collapse punctuation to spaces (keeps / % $ € £ ¥ °). Matching only."""
    text = unicodedata.normalize("NFKD", text.replace("’", "'"))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"[^\w/%$€£¥°]+|_", " ", text).strip()


_LOOKUP = {alias: code for code, (_, names) in _UNITS.items() for alias in names.split("|")}
_LOOKUP.update({code: code for code in _UNITS})


def unit_code(text: str) -> str | None:
    """Canonical code for a printed unit ('Kg.', 'cuillères à soupe', 'bouteilles') or None."""
    key = fold(text)
    if key in _LOOKUP:
        return _LOOKUP[key]
    key = re.sub(r"^(?:de|d|of)\s+", "", key)
    return _LOOKUP.get(key)


def convert(amount: Fraction, source: str, target: str) -> Fraction | None:
    """Exact conversion within mass or volume; None for anything else (including culinary units)."""
    if source == target and source in DIMENSION:
        return amount
    if source in _FACTOR and target in _FACTOR and DIMENSION[source] == DIMENSION[target]:
        return amount * _FACTOR[source] / _FACTOR[target]
    return None


# --- currency ---------------------------------------------------------------------------------
ISO_CODES = frozenset("""AED ARS AUD BGN BRL CAD CHF CLP CNY COP CZK DKK DZD EGP EUR GBP HKD HUF IDR ILS INR ISK JPY KRW
MAD MXN MYR NGN NOK NZD PEN PHP PKR PLN RON RUB SAR SEK SGD THB TND TRY TWD UAH USD VND XAF XOF ZAR""".split())
MINOR_UNITS = {"JPY": 0, "KRW": 0, "CLP": 0, "VND": 0, "ISK": 0, "XAF": 0, "XOF": 0, "TND": 3}
_SYMBOLS = {"€": ("EUR",), "euro": ("EUR",), "euros": ("EUR",), "£": ("GBP",), "us$": ("USD",), "u s $": ("USD",),
            "c$": ("CAD",), "ca$": ("CAD",), "can$": ("CAD",), "a$": ("AUD",), "au$": ("AUD",), "nz$": ("NZD",),
            "₹": ("INR",), "₩": ("KRW",), "zl": ("PLN",), "kc": ("CZK",), "fr": ("CHF",), "sfr": ("CHF",),
            "$": ("USD", "CAD", "AUD"), "¥": ("JPY", "CNY"), "kr": ("SEK", "NOK", "DKK")}
_SYMBOL_PATTERN = sorted(list(_SYMBOLS) + sorted(ISO_CODES, key=str.lower), key=len, reverse=True)


def currency_candidates(text: str) -> tuple[str, ...]:
    """All ISO codes a printed currency could mean: one = resolved, several = ambiguous, none = unknown."""
    key = fold(text).replace("fr.", "fr")
    if key.upper() in ISO_CODES:
        return (key.upper(),)
    return _SYMBOLS.get(key, ())


def split_currency(raw: str) -> tuple[tuple[str, ...], str]:
    """Strip currency markers printed at either end of an amount; return (candidates, remainder).

    A sign or parenthesis may sit outside the marker ('-$11.95', '$-11.95', '($11.95)', '11,95 €-'); it is
    kept, in printed order relative to the digits, in the remainder. The marker's spelling is untouched.
    """
    text = raw.strip()
    lead = re.sub(r"\s", "", re.match(r"[\s+\-\u2212\u2013(]*", text).group())
    text = text[len(re.match(r"[\s+\-\u2212\u2013(]*", text).group()):]
    tail_match = re.search(r"[\s+\-\u2212\u2013)]*$", text)
    tail = re.sub(r"\s", "", tail_match.group())
    text = text[:tail_match.start()]
    found = ()
    for _ in range(2):
        for token in _SYMBOL_PATTERN:
            word = re.fullmatch(r"[A-Za-z]+", token) is not None
            lead_hit = re.match(rf"{re.escape(token)}{r'\b' if word else ''}\s*", text, re.IGNORECASE)
            trail_hit = re.search(rf"\s*{r'\b' if word else ''}{re.escape(token)}$", text, re.IGNORECASE)
            hit = lead_hit or trail_hit
            if hit:
                found = found or currency_candidates(token)
                text = (text[hit.end():] if hit is lead_hit else text[:hit.start()]).strip()
                # a sign between the marker and the digits ('$-11.95') belongs to the amount
                inner = re.match(r"[\s+\-\u2212\u2013]+", text)
                if inner and hit is lead_hit:
                    lead += re.sub(r"\s", "", inner.group())
                    text = text[inner.end():]
                break
    return found, lead + text.strip() + tail


def currency_in_amount(raw: str) -> tuple[str, ...]:
    """Candidates for a currency marker printed next to an amount ('12,50 €', 'EUR 3.00', '$4')."""
    return split_currency(raw)[0]
