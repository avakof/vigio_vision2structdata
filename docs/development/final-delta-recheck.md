# Recheck of the three delta findings

**Two findings pass. The bilingual pairing finding remains open for distant cells within one Paddle table region.** The original five-line reproducer now matches the lead's observations, and all 16 focused regression tests pass. The additional table case reproduced through the hybrid adapter, domain assembly and human review API.

Reviewed `6214a2cb9564439875165cb08d090c222cd2b77a`, including `cce3e6704eb01cbfddc4897e762a040292259f3c`. The verification worktree had the same Git tree, `6439eb21bbf1113d7e80a94260a220c836684332`. Python 3.12.16, pytest 9.2.0 and SQLite 3.53.1 ran locally. This check used synthetic inputs and controlled adapter replies. The native feasibility evidence is unchanged. [Original findings](final-delta-audit.md) · [Original reproducer](../../evidence/development/reasoner-delta.md)

| Original finding | Verdict | Observed behavior |
| --- | --- | --- |
| 1. Version-specific document type | **passed** | At v2 the list, standalone row, CSV, JSON and embedded snapshot all said `menu`. After a v3 correction on v1, the list returned v3 `invoice`. Re-exporting all three at that point yielded v1 `invoice` with ancestry [1], v2 `menu` with [1,2], and v3 `invoice` with [1,3]. Every standalone file held exactly the selected full snapshot, matched the source store’s lineage hashes and passed `PRAGMA foreign_key_check`. |
| 2. Layout support for a bilingual pairing | **failed** for a multi-column parser table | The page-only, one-sided native and coarse-crop cases now emit `translation_without_layout_evidence` at review severity and block approval. Distinct distant text boxes also block. A shared native line and adjacent, aligned spans allow the pairing. For a page-only pair, approval alone failed, an unrelated price change left the blocker intact, and explicit human confirmation of the translated field permitted approval. One large table region still conceals a distant-column mismatch, as reproduced below. |
| 3. Empty decoded text block | **passed** | Alongside a readable total, an empty text crop with EOS produced two retained regions, one degraded block, the `parser_content_incomplete` issue, page 1 incomplete and normalized total `37.25`. Blank or whitespace-only text, empty table cells, blank chart and seal cases retained a grounded unread box. Blank `image`, `header_image` and `footer_image` controls added no degradation alongside usable text. A page containing only unread text ended with `no_extraction` and retained the unread region in its page artifact. |

## Remaining counterexample for finding 2

The controlled page showed **Soupe** under **Entrées** at x=20 and **Roast chicken** under **Mains** at x=750. Paddle’s controlled output carried one table box covering (10,70)–(980,190). The second row put those two names in its first and fourth cells, with two empty intervening cells. A controlled model reading proposed the second dish as the translation of the first, citing the table for both. Production assembly returned `valid`, `needs_review` and zero issues. A generic approval event with zero field edits returned `human_approved`.

The table adapter keeps cell boundaries as tabs. When both names cite the same region, `_paired_translation` folds those tabs into whitespace and accepts adjacent text on the line. That rule loses the column separation the parser actually supplied. Preserve cell boundaries when matching a shared table region. Keep this pair in review unless both readings have an evidenced relation within a cell or supporting alignment or explicit human confirmation. Add the separated-cell case alongside the shared-line and distant-box tests. [Adapter](../../src/vision_tables/vision.py) · [Pairing rule](../../src/vision_tables/domain/rules.py)

This reduced case reproduces the same result within the core environment. The end-to-end recheck also exercised the model-facing payload validator and evidence links.

```python
from vision_tables import vision
from vision_tables.contracts import PageReading
from vision_tables.domain import assemble, review_gate
from vision_tables.domain.examples import example_context, f, rec

ingested, run = example_context(pipeline="hybrid")
html = ("<table><tr><td>Entrées</td><td></td><td></td><td>Mains</td></tr>"
        "<tr><td>Soupe</td><td></td><td></td><td>Roast chicken</td></tr></table>")
regions, _ = vision._parser_regions(ingested.pages[0], {
    "width": 1000, "height": 1400, "blocks": [
        {"block_label": "table", "block_content": html, "block_bbox": [10, 70, 980, 190]}]}, 0)
region_id = regions[0].id
reading = PageReading(page=1, doc_type="menu", regions=regions, records=[
    rec("menu", "m", venue_name=f("Bistro"), currency=f("EUR")),
    rec("menu_item", "i", "m", price=f("4,50"),
        name=f("Soupe", language="fr", region_id=region_id),
        translated_name=f("Roast chicken", language="en", region_id=region_id)),
])
doc = assemble(ingested, [reading], [1], run, "doc")
print(doc.validation, [issue.code for issue in doc.issues], review_gate(doc)["approvable"])
# Observed: valid [] True
```

## Targeted checks

I ran the unchanged Python block from `43a362a:evidence/development/reasoner-delta.md` first, then checked all three historical exports after the fork and ran the menu and blank-block controls. The following automated selection passed 16 tests in 0.40 s.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src /Users/vakof/orca/vision-to-tables/.venv/bin/python -B -m pytest -q -p no:cacheprovider \
  tests/test_store_exports.py::test_reclassification_lists_latest_and_preserves_exported_type_per_version \
  tests/test_domain_assemble.py::test_menu_translation_requires_evidence_for_the_pair \
  tests/test_vision_parser_tokens.py::test_blank_text_bearing_block_preserves_box_and_marks_coverage_incomplete \
  tests/test_vision_parser_tokens.py::test_blank_non_text_image_block_does_not_degrade_coverage \
  tests/test_vision_parser_tokens.py::test_blank_only_parser_output_cannot_succeed_extraction
```

Dispatch `ctx_d6bbbb55290c`, task `task_18d856ef6a22`. The independent Codex turn `01a213b7-2036-78e0-9b92-a3e937a88f1d` recorded `gpt-6.2-astra` and `xhigh`.
