"""Generate checked-in JSON Schemas from the installed package."""
from pathlib import Path

from jsonschema import Draft202012Validator

from vision_tables.artifacts import write_json
from vision_tables.contracts import CorrectionSet, Document, Ingested, ModelPayload, PageReading


def main():
    root = Path(__file__).resolve().parents[1] / "schemas"
    for name, model in (("document", Document), ("corrections", CorrectionSet),
                        ("model-payload", ModelPayload), ("ingestion", Ingested), ("page-reading", PageReading)):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(schema)
        write_json(root / f"{name}.json", schema)


if __name__ == "__main__":
    main()
