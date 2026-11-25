"""Small documented PageReading examples, one per document type, plus the context to assemble them.

These are exactly what a model adapter should produce: raw text as printed, a supporting quote,
alternatives only for real uncertainty, no calculated fields and no geometry. Normalization,
arithmetic and relationships are the domain's job.

    from vision_tables.domain.examples import example_context, EXAMPLES
    ingested, run = example_context()
    document = assemble(ingested, [EXAMPLES["invoice"]()], [1], run, "doc-example")
"""
from __future__ import annotations

from ..config import PIPELINE_VERSION, SCHEMA_VERSION
from ..contracts import FieldRead, Ingested, Page, PageReading, RecordRead, Run, Source


def f(raw: str, evidence: str | None = None, **kw) -> FieldRead:
    """A readable field: ``raw`` exactly as printed, ``evidence`` the surrounding text (defaults to raw)."""
    return FieldRead(raw=raw, evidence=raw if evidence is None else evidence, **kw)


def rec(kind: str, local_id: str, parent_id: str | None = None, **fields: FieldRead) -> RecordRead:
    return RecordRead(kind=kind, local_id=local_id, parent_id=parent_id, fields=fields)


def invoice_page(page: int = 1) -> PageReading:
    """French invoice: day-first date inferred from the other date, pack arithmetic, tax base x rate."""
    return PageReading(page=page, doc_type="invoice", records=[
        rec("invoice", "inv", document_kind=f("FACTURE"), document_number=f("FA-2026-0042", "Facture n° FA-2026-0042"),
            supplier_name=f("Maison Dupont SARL"), issue_date=f("03/04/2026", "Date : 03/04/2026"),
            due_date=f("30/04/2026", "Échéance : 30/04/2026"), currency=f("€", "Montants en €"),
            subtotal=f("25,70", "Total HT 25,70"), tax_total=f("1,41"), total=f("27,11 €", "Total TTC 27,11 €")),
        rec("line_item", "l1", "inv", description=f("Eau minérale 1 L"), quantity=f("2"), purchase_unit=f("cartons"),
            pack_count=f("6", "carton de 6 bouteilles"), pack_unit_quantity=f("1", "bouteilles de 1 L"), pack_unit=f("L", "1 L"),
            unit_price=f("0,60"), price_basis=f("le litre"), line_total=f("7,20")),
        rec("line_item", "l2", "inv", description=f("Farine T55 25 kg"), quantity=f("1"), purchase_unit=f("sac"),
            unit_price=f("18,50"), line_total=f("18,50")),
        rec("tax", "t1", "inv", name=f("TVA 5,5 %"), basis=f("25,70"), rate=f("5,5 %"), amount=f("1,41")),
    ])


def menu_page(page: int = 1) -> PageReading:
    """Bilingual menu: translation beside the original, variants, a supplement and a bundle choice."""
    return PageReading(page=page, doc_type="menu", records=[
        rec("menu", "m", venue_name=f("Chez Marcel"), currency=f("EUR", "Prix en EUR")),
        rec("section", "s1", "m", name=f("Entrées", "Entrées / Starters"), translated_name=f("Starters", "Entrées / Starters")),
        rec("menu_item", "i1", "s1", name=f("Soupe à l'oignon"), translated_name=f("French onion soup"), price=f("9,50", "Soupe à l'oignon ..... 9,50")),
        rec("menu_item", "i2", "s1", name=f("Salade verte")),
        rec("variant", "v1", "i2", name=f("petite"), price=f("4,50")),
        rec("variant", "v2", "i2", name=f("grande"), price=f("7,00")),
        rec("supplement", "x1", "i1", name=f("Gratiné au fromage"), price=f("+ 1,50")),
        rec("bundle", "b1", "s1", name=f("Menu du jour"), price=f("24,00")),
        rec("bundle_choice", "c1", "b1", name=f("Soupe à l'oignon"), item_ref=f("i1", "Soupe à l'oignon"), group=f("Entrée")),
    ])


def recipe_page(page: int = 1) -> PageReading:
    """Handwritten recipe: exact fraction, range, imprecise quantity, scale-explicit temperature, no yield."""
    return PageReading(page=page, doc_type="recipe", records=[
        rec("recipe", "r", title=f("Gâteau au yaourt")),
        rec("ingredient", "g1", "r", name=f("farine"), quantity=f("⅓"), unit=f("tasse")),
        rec("ingredient", "g2", "r", name=f("huile"), quantity=f("2"), unit=f("cuillères à soupe")),
        rec("ingredient", "g3", "r", name=f("ail"), quantity=f("2-3"), unit=f("gousses"), optional=f("optionnel")),
        rec("ingredient", "g4", "r", name=f("sel"), quantity=f("au goût")),
        rec("substitution", "u1", "g2", name=f("beurre fondu"), quantity=f("2"), unit=f("c. à soupe")),
        rec("step", "p1", "r", instruction=f("Mélanger puis cuire 35 min à 180°C"), duration_minutes=f("35 min"), temperature_c=f("180°C")),
    ])


EXAMPLES = {"invoice": invoice_page, "menu": menu_page, "recipe": recipe_page}


def example_context(pages: int = 1, *, pipeline: str = "direct") -> tuple[Ingested, Run]:
    """A synthetic ingested source (no files) and run record for assembling the examples."""
    source = Source(sha256="0" * 64, media_type="image/png", size_bytes=1, page_count=pages, original_path="source.png")
    page_rows = [Page(number=n, width=1000, height=1400, unit="px", image_path=f"pages/{n}.png", image_width=1000,
                      image_height=1400, image_to_original=(1, 0, 0, 1, 0, 0)) for n in range(1, pages + 1)]
    run = Run(job_id="job-example", pipeline=pipeline, schema_version=SCHEMA_VERSION, pipeline_version=PIPELINE_VERSION,
              model="example", revision="0", runtime={}, settings={}, config_hash="1" * 64)
    return Ingested(source=source, pages=page_rows), run
