# Scorer follow-up

The original findings and reproductions remain in [Integrated CLI QA](qa-integrated.md). This follow-up corrects findings 3 and 4. The independent domain fix for findings 1 and 2 is commit `195d483`.

The synthetic labels now expect normalized litre code `l`, separate original and replacement quantities for the struck ingredient, the boolean crossed-out flag and the production issue code `model_token_limit`. The manifest records the updated label digests. Source files and archival labels retain their existing digests.

Missing recipe yield now requires a completed source, full page coverage of the recipe, no suspected cutoff, one identified recipe root, readable recipe content with scoped page evidence, and a document-level `recipe_yield_missing` issue. A value, ambiguous or unreadable reading, raw yield text or alternatives for quantity, unit or type blocks credit. A supported absence belongs to exact-match scoring. Present fields alone count toward answerable-field coverage. Step time and temperature have their own metric and still contribute to field exact match.

Regression tests cover partially processed and unavailable sources, empty and unclassified documents, contradictory yield readings, valid absence and separate metric denominators. The CLI test uses a fixed injected runtime response to exercise production assembly and evaluation. Four former conditional expected failures now run as ordinary assertions. All checks are independent of model predictions, and the inference lock was unused.
