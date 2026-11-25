"""Domain extraction, normalization and review policy.

Entry points (see docs/development/domain.md): ``assemble``, ``revalidate``, ``apply_corrections``,
``review_gate``, the vocabulary (``vocabulary()``, ``model_vocabulary()``) and ``EXAMPLES``.
"""
from .build import assemble
from .catalog import CATALOG, review_gates
from .corrections import apply_corrections, review_gate, revalidate
from .schema import FIELD_TYPES, VOCABULARY_VERSION, model_vocabulary, vocabulary

__all__ = ["CATALOG", "FIELD_TYPES", "VOCABULARY_VERSION", "apply_corrections", "assemble", "model_vocabulary",
           "review_gate", "review_gates", "revalidate", "vocabulary"]
