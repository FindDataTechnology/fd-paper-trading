"""Vendored fd_quant contract layer (mirror; see ../VENDOR.json).

Upstream's ``fd_quant/__init__.py`` re-exports the same names via absolute
``fd_quant.…`` imports; this glue uses relative imports so the mirror
resolves under ``fd_paper_trading._vendor``. The exported set is upstream's
(incl. ``check_rating`` / ``check_target_weights`` / ``load_schema`` and the
``SUPPORTED_SCHEMA_VERSION`` / ``WEIGHT_SUM_TOLERANCE`` constants).
"""
from .contracts.models import (  # noqa: F401
    SUPPORTED_SCHEMA_VERSION,
    WEIGHT_SUM_TOLERANCE,
    ContractError,
    Evidence,
    Producer,
    Rating,
    TargetWeights,
    check_rating,
    check_target_weights,
    load_schema,
    validate,
    validate_rating,
    validate_target_weights,
)
from .contracts.unsourced import unsourced_fields  # noqa: F401

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "WEIGHT_SUM_TOLERANCE",
    "ContractError",
    "Evidence",
    "Producer",
    "Rating",
    "TargetWeights",
    "check_rating",
    "check_target_weights",
    "load_schema",
    "unsourced_fields",
    "validate",
    "validate_rating",
    "validate_target_weights",
]
