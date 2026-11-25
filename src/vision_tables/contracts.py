"""Canonical wire types shared by ingestion, inference, domain and persistence."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import DocType, PIPELINE_VERSION, SCHEMA_VERSION

Number = Annotated[int, Field(ge=1)]
Identifier = Annotated[str, Field(min_length=1, max_length=180, pattern=r"^[A-Za-z0-9_.:-]+$")]
Sha = Annotated[str, Field(pattern="^[0-9a-f]{64}$")]
Text = Annotated[str, Field(max_length=16384)]
Scalar = str | bool | None
State = Literal["readable", "ambiguous", "unreadable", "not_present"]
Origin = Literal["observed", "calculated", "human_supplied"]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Source(Wire):
    sha256: Sha
    media_type: str
    size_bytes: Number
    page_count: Number
    original_path: str  # Relative to the job artifact root


class Page(Wire):
    number: Number
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    unit: Literal["px", "pt"]
    image_path: str
    image_width: Number
    image_height: Number
    # x = a*u + c*v + e, y = b*u + d*v + f. Original origin is top-left.
    image_to_original: tuple[float, float, float, float, float, float]
    text_layer: Literal["absent", "usable", "partial", "unreliable"] = "absent"

    @model_validator(mode="after")
    def invertible(self):
        a, b, c, d, *_ = self.image_to_original
        if math.isclose(a * d, b * c, rel_tol=1e-12, abs_tol=1e-14):
            raise ValueError("singular page transform")
        return self


class Box(Wire):
    xyxy: tuple[float, float, float, float]
    space: Literal["original_top_left"] = "original_top_left"
    precision: Literal["native_text", "parser_region", "crop"]

    @model_validator(mode="after")
    def area(self):
        x0, y0, x1, y1 = self.xyxy
        if not (0 <= x0 < x1 and 0 <= y0 < y1):
            raise ValueError("invalid grounded rectangle")
        return self


class Region(Wire):
    id: Identifier
    page: Number
    order: int = Field(ge=0)
    kind: str
    text: Text = ""
    language: str | None = None
    writing: Literal["print", "handwriting", "mixed", "unknown"] = "unknown"
    box: Box | None = None


class Issue(Wire):
    code: Identifier
    message: str
    page: Number | None = None
    observation_ids: list[Identifier] = Field(default_factory=list)


class Ingested(Wire):
    source: Source
    pages: list[Page]
    regions: list[Region] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    skipped_pages: list[Number] = Field(default_factory=list)


class FieldRead(Wire):
    raw: Text | None = None
    evidence: Text = ""
    region_id: Identifier | None = None
    state: State = "readable"
    alternatives: list[Text] = Field(default_factory=list, max_length=16)
    reason: Text | None = None
    language: str | None = None


class RecordRead(Wire):
    kind: Identifier
    local_id: Identifier
    parent_id: Identifier | None = None
    fields: dict[Identifier, FieldRead] = Field(max_length=100)


class PageReading(Wire):
    page: Number
    doc_type: DocType
    records: list[RecordRead] = Field(default_factory=list, max_length=1024)
    regions: list[Region] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    incomplete: bool = False
    suspected_cutoff: bool = False
    calls: list[dict] = Field(default_factory=list)


class Evidence(Wire):
    page: Number
    region_id: Identifier | None = None
    quote: Text = ""


class Derivation(Wire):
    calculation: str = Field(min_length=1)
    inputs: list[Identifier] = Field(min_length=1)


class Observation(Wire):
    id: Identifier
    entity_id: Identifier
    field: Identifier
    raw: Text | None
    value: Scalar
    state: State
    origin: Origin = "observed"
    language: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    alternatives: list[Text] = Field(default_factory=list)
    reason: Text | None = None
    derivation: Derivation | None = None

    @model_validator(mode="after")
    def reading(self):
        if self.state != "readable" and self.value is not None:
            raise ValueError("unresolved observation must have null value")
        if self.origin == "observed" and not self.evidence:
            raise ValueError("observed fields require page evidence")
        if (self.origin == "calculated") != bool(self.derivation):
            raise ValueError("calculated observations require a derivation")
        return self


class Entity(Wire):
    id: Identifier
    kind: Identifier
    parent_id: Identifier | None = None
    page: Number
    ordinal: int = Field(ge=0)


class Run(Wire):
    job_id: Identifier
    pipeline: Literal["direct", "hybrid"]
    schema_version: str = SCHEMA_VERSION
    pipeline_version: str = PIPELINE_VERSION
    model: str
    revision: str
    runtime: dict
    settings: dict
    config_hash: Sha
    calls: list[dict] = Field(default_factory=list)


class Coverage(Wire):
    processed_pages: list[Number]
    skipped_pages: list[Number] = Field(default_factory=list)
    incomplete_pages: list[Number] = Field(default_factory=list)
    suspected_capture: bool = False
    original_capture: Literal["not_established", "suspected_incomplete"] = "not_established"


class Correction(Wire):
    observation_id: Identifier
    value: Scalar
    raw: Text | None = None
    state: State = "readable"
    reason: str = Field(min_length=1, max_length=4096)


class CorrectionSet(Wire):
    document_id: Identifier
    base_version: Number
    reviewer: str = Field(min_length=1, max_length=256)
    changes: list[Correction] = Field(min_length=1, max_length=1000)
    approve: bool = False


class Document(Wire):
    document_id: Identifier
    version: Number = 1
    parent_version: Number | None = None
    doc_type: DocType
    scope: list[Number] = Field(min_length=1)
    source: Source
    pages: list[Page]
    regions: list[Region]
    run: Run
    coverage: Coverage
    entities: list[Entity]
    observations: list[Observation]
    issues: list[Issue]
    validation: Literal["valid", "issues", "incomplete"] = "issues"
    approval: Literal["needs_review", "human_approved"] = "needs_review"
    corrections: list[CorrectionSet] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        def indexed(rows):
            items = {item.id: item for item in rows}
            if len(items) != len(rows):
                raise ValueError("duplicate ID")
            return items

        page_map = {page.number: page for page in self.pages}
        scope = set(self.scope)
        if (self.scope != sorted(scope) or max(scope) > self.source.page_count
                or len(page_map) != len(self.pages) or not page_map.keys() <= scope):
            raise ValueError("invalid document scope or page reference")
        regions, entities, observations = map(indexed, (self.regions, self.entities, self.observations))
        processed = set(self.coverage.processed_pages)
        skipped = set(self.coverage.skipped_pages)
        incomplete = set(self.coverage.incomplete_pages)
        if processed & skipped or not processed <= page_map.keys() or not incomplete <= page_map.keys():
            raise ValueError("inconsistent page coverage")
        if processed | skipped | incomplete != scope:
            raise ValueError("scope contains unaccounted pages")
        if (skipped or incomplete) and self.validation != "incomplete":
            raise ValueError("incomplete coverage must be visible")
        if self.approval == "human_approved" and (self.validation != "valid" or self.issues):
            raise ValueError("unresolved document cannot be approved")
        for region in self.regions:
            page = page_map.get(region.page)
            if page is None:
                raise ValueError("unknown region page")
            if region.box and (region.box.xyxy[2] > page.width + 0.001 or region.box.xyxy[3] > page.height + 0.001):
                raise ValueError("region outside original page")
        for entity in self.entities:
            if entity.page not in page_map or entity.parent_id is not None and entity.parent_id not in entities:
                raise ValueError("unknown entity page or parent")
        for obs in self.observations:
            if obs.entity_id not in entities:
                raise ValueError("orphan observation")
            for ref in obs.evidence:
                if ref.page not in page_map or ref.region_id and (
                    ref.region_id not in regions or regions[ref.region_id].page != ref.page
                ):
                    raise ValueError("unknown or mismatched evidence reference")
        for issue in self.issues:
            if issue.page is not None and issue.page not in scope or set(issue.observation_ids) - observations.keys():
                raise ValueError("unknown issue reference")

        def check_dag(graph):
            done = set()
            for node in graph:
                stack = [(node, False)]
                pending = set()
                while stack:
                    key, leaving = stack.pop()
                    if key in done:
                        continue
                    if leaving:
                        done.add(key)
                        pending.discard(key)
                    else:
                        if key in pending or key not in graph:
                            raise ValueError("cyclic or missing dependency")
                        pending.add(key)
                        stack.append((key, True))
                        stack.extend((parent, False) for parent in graph[key])
        check_dag({e.id: [e.parent_id] if e.parent_id else [] for e in self.entities})
        check_dag({o.id: o.derivation.inputs if o.derivation else [] for o in self.observations})
        return self
