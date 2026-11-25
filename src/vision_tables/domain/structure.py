"""Apply ordered reviewer row and field operations to a private candidate snapshot."""
from ..config import ProcessingError
from ..contracts import AddField, AddRow, Entity, Evidence, Observation, RemoveRow
from ..vocabulary import CALCULATED_ONLY, FAMILIES
from .schema import FIELD_TYPES, PARENTS, REQUIRED_PARENT


def edit_structure(document, event, check_value):
    if not event.structure:
        return document
    entities = {row.id: row.model_copy() for row in document.entities}
    order = [row.id for row in sorted(entities.values(), key=lambda r: (r.page, r.ordinal))]
    fields = {(o.entity_id, o.field): o for o in document.observations if o.origin != "calculated"}
    pages = {p.number for p in document.pages}
    regions = {r.id: r.page for r in document.regions}
    reserved = set(entities)
    for previous in document.corrections:
        reserved.update(op.entity_id for op in previous.structure if isinstance(op, RemoveRow))

    def invalid(message):
        raise ProcessingError("invalid_correction", message)

    def add_field(entity, name, reading, reason):
        key = (entity.id, name)
        if key in fields or name not in FIELD_TYPES[entity.kind] or name in CALCULATED_ONLY:
            invalid(f"Cannot add duplicate, calculated or unsupported {entity.kind}.{name}")
        lexical = FIELD_TYPES[entity.kind][name]
        error = check_value(lexical, reading.value, entities)
        if error:
            invalid(f"{name}: {error}")
        if not reading.raw.strip() or reading.region_id and regions.get(reading.region_id) != entity.page:
            invalid("A human reading requires source text and a matching page or region")
        fields[key] = Observation(
            id=f"{entity.id}.{name}", entity_id=entity.id, field=name, raw=reading.raw, value=reading.value,
            state="readable", origin="human_supplied", reason=reason,
            evidence=[Evidence(page=entity.page, region_id=reading.region_id, quote=reading.raw, channel="human")],
        )

    for op in event.structure:
        if isinstance(op, AddRow):
            if op.entity_id in reserved or len(op.entity_id) > 140:
                invalid(f"Duplicate, retired or overlong row ID: {op.entity_id}")
            if op.page not in pages or op.kind not in FAMILIES[document.doc_type]:
                invalid("Row kind or evidence page is outside the document")
            parent = entities.get(op.parent_id)
            if op.parent_id and (not parent or parent.kind not in PARENTS[op.kind]):
                invalid("Invalid parent for the new row")
            if not parent and op.kind in REQUIRED_PARENT:
                invalid("New row requires a parent")
            if op.after_entity_id:
                anchor = entities.get(op.after_entity_id)
                if anchor is None or anchor.page != op.page:
                    invalid("Row ordering anchor must be on the same page")
                index = order.index(anchor.id) + 1
            else:
                index = next((i for i, eid in enumerate(order) if entities[eid].page > op.page), len(order))
            row = Entity(id=op.entity_id, kind=op.kind, page=op.page, parent_id=op.parent_id, ordinal=0)
            entities[row.id] = row
            reserved.add(row.id)
            order.insert(index, row.id)
            for name, reading in op.fields.items():
                add_field(row, name, reading, op.reason)
        elif isinstance(op, AddField):
            if op.entity_id not in entities:
                invalid("Cannot add a field to an unknown row")
            add_field(entities[op.entity_id], op.field, op.reading, op.reason)
        else:
            if op.entity_id not in entities:
                invalid("Cannot remove an unknown row")
            del entities[op.entity_id]
            order.remove(op.entity_id)
            fields = {key: obs for key, obs in fields.items() if key[0] != op.entity_id}
    for index, eid in enumerate(order):
        row = entities[eid]
        if row.parent_id and row.parent_id not in entities:
            invalid("Removal would orphan a child; explicitly remove all affected children")
        row.ordinal = index
    corrected = {change.observation_id for change in event.changes}
    if any(obs.field == "item_ref" and obs.state == "readable" and obs.value not in entities
           and obs.id not in corrected for obs in fields.values()):
        invalid("Removal would orphan a bundle choice; correct its reference or remove the choice")
    return document.model_copy(update={"entities": [entities[eid] for eid in order],
                                       "observations": list(fields.values())})
