"""Project-local paths and reproducible extraction settings."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "1.0"
PIPELINE_VERSION = "0.1.0"
QWEN_MODEL = "mlx-community/Qwen3.5-9B-MLX-4bit"
QWEN_REVISION = "938d8919941c6e7efd3c7150eff7fe9d12afa631"
DocType = Literal["invoice", "menu", "recipe", "unknown"]


def digest(data: object) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def default_state() -> Path:
    return Path(os.getenv("VT_STATE_DIR", ".vt-state")).resolve()


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    state_dir: Path = Field(default_factory=default_state)
    lock_file: Path | None = None
    pipeline: Literal["direct", "hybrid"] = "direct"
    doc_type: Literal["auto", "invoice", "menu", "recipe"] = "auto"
    page_groups: tuple[tuple[int, ...], ...] = ()
    max_bytes: int = Field(default=64 << 20, gt=0, le=512 << 20)
    max_pages: int = Field(default=16, gt=0, le=100)
    max_pixels: int = Field(default=40_000_000, gt=0, le=100_000_000)
    render_pixels: int = Field(default=2_000_000, gt=0, le=8_000_000)
    max_crops: int = Field(default=1, ge=0, le=4)
    max_tokens: int = Field(default=2048, ge=32, le=8192)
    retries: int = Field(default=1, ge=0, le=2)

    @model_validator(mode="after")
    def validate_groups(self):
        flattened = [n for group in self.page_groups for n in group]
        if any(not group or list(group) != sorted(set(group)) for group in self.page_groups):
            raise ValueError("each page group must be nonempty and in source order")
        if len(set(flattened)) != len(flattened) or any(n < 1 for n in flattened):
            raise ValueError("page groups must be disjoint and one-based")
        return self

    @property
    def inference_lock(self) -> Path:
        override = os.getenv("VT_INFERENCE_LOCK")
        if self.lock_file or override:
            return Path(self.lock_file or override).resolve()
        return (self.state_dir / "inference.lock").resolve()

    def content_settings(self) -> dict:
        return self.model_dump(mode="json", exclude={"state_dir", "lock_file"})


class ProcessingError(Exception):
    """An actionable document or runtime failure suitable for structured CLI output."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
