import pytest
from pydantic import ValidationError

from vision_tables.config import Settings, digest
from vision_tables.contracts import Box, Observation, Page
from vision_tables.pipeline import page_scopes


def test_ambiguous_value_cannot_be_silently_resolved():
    with pytest.raises(ValidationError, match="unresolved"):
        Observation(id="o1", entity_id="e1", field="date", raw="03/04/26", value="2026-04-03",
                    state="ambiguous", evidence=[{"page": 1, "quote": "03/04/26"}])


def test_calculation_needs_inputs_and_source_read_needs_evidence():
    kwargs = dict(id="o1", entity_id="e1", field="price", raw="3,00", value="3.00", state="readable")
    with pytest.raises(ValidationError, match="page evidence"):
        Observation(**kwargs)
    with pytest.raises(ValidationError, match="derivation"):
        Observation(**kwargs, origin="calculated")


def test_geometry_must_be_finite_and_invertible():
    with pytest.raises(ValidationError):
        Box(xyxy=(0, 0, float("nan"), 2), precision="crop")
    with pytest.raises(ValidationError, match="singular"):
        Page(number=1, width=12, height=10, unit="px", image_path="1.png",
             image_width=12, image_height=10, image_to_original=(1, 2, 2, 4, 0, 0))


def test_grouping_is_stable_and_disjoint():
    settings = Settings(page_groups=((1, 3),))
    assert page_scopes(5, settings, {1, 2, 3, 4}) == [[1, 3], [2], [4]]
    with pytest.raises(ValidationError):
        Settings(page_groups=((2, 1),))
    with pytest.raises(ValidationError):
        Settings(page_groups=((1,), (1, 2)))


def test_semantic_config_hash_excludes_paths(tmp_path):
    first = Settings(state_dir=tmp_path)
    other = Settings(state_dir=tmp_path / "elsewhere", lock_file=tmp_path / "lock")
    assert digest(first.content_settings()) == digest(other.content_settings())
    assert digest(first.content_settings()) != digest(Settings(max_tokens=256).content_settings())
