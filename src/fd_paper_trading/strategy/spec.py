"""Dual-track strategy spec models (design D1, spec: strategy-management).

A strategy's persisted ``spec`` (JSONB) is exactly one of:

- Template track: ``{"kind": "template", "template": <id>, "symbol": <sym>,
  "params": {...}}``
- Code track: ``{"kind": "code", "symbol": <sym>, "source": <python>}``

``validate_spec`` is the single entry point — it returns a normalized spec
(template params have catalog defaults baked in, symbols uppercased) or
raises ``SpecValidationError`` with one human-readable message per violation
(structured for LLM self-repair in the conversation tools).
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from .templates import TemplateValidationError, validate_template_params

_SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,15}")


class SpecValidationError(ValueError):
    """Spec validation failure with one message per violation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def _clean_symbol(v: object) -> str:
    if not isinstance(v, str) or not v.strip():
        raise ValueError("symbol must be a non-empty string")
    s = v.strip().upper()
    if not _SYMBOL_RE.fullmatch(s):
        raise ValueError(
            f"symbol {v!r} is invalid: use 1-16 chars of A-Z, 0-9, '.', '-'"
        )
    return s


class _BaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str

    @field_validator("symbol")
    @classmethod
    def _symbol_ok(cls, v: object) -> str:
        return _clean_symbol(v)


class TemplateSpec(_BaseSpec):
    kind: Literal["template"]
    template: str
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _params_ok(self) -> "TemplateSpec":
        try:
            self.params = validate_template_params(self.template, self.params)
        except TemplateValidationError as exc:
            raise ValueError("; ".join(exc.errors)) from exc
        return self


class CodeSpec(_BaseSpec):
    kind: Literal["code"]
    source: str

    @field_validator("source")
    @classmethod
    def _source_ok(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("source must be non-empty Python defining on_bar(ctx)")
        return v


StrategySpec = Annotated[TemplateSpec | CodeSpec, Field(discriminator="kind")]
_adapter = TypeAdapter(StrategySpec)


def validate_spec(raw: object) -> TemplateSpec | CodeSpec:
    """Validate a raw spec dict; return the normalized typed spec.

    Raises ``SpecValidationError`` listing every violation. Code-track specs are
    also AST-validated here (design D6) so create/update, backtest, and the live
    runner all reject unsafe source at the same chokepoint.
    """
    if not isinstance(raw, dict):
        raise SpecValidationError(["spec must be an object"])
    try:
        spec = _adapter.validate_python(raw)
    except ValidationError as exc:
        raise SpecValidationError([_format_error(e) for e in exc.errors()]) from exc
    if isinstance(spec, CodeSpec):
        from .codegen import validate_source

        errors = validate_source(spec.source)
        if errors:
            raise SpecValidationError(errors)
    return spec


def _format_error(err: dict) -> str:
    parts = list(err.get("loc", ()))
    # Discriminated-union errors are prefixed with the variant tag; drop it.
    if parts and parts[0] in ("template", "code"):
        parts = parts[1:]
    loc = ".".join(str(part) for part in parts)
    msg = err.get("msg", "invalid")
    return f"{loc}: {msg}" if loc else msg


def spec_summary(spec: TemplateSpec | CodeSpec) -> str:
    """One-line human summary for lists/cards."""
    if isinstance(spec, TemplateSpec):
        params = ", ".join(f"{k}={v}" for k, v in sorted(spec.params.items()))
        return f"{spec.template}({params}) on {spec.symbol}"
    return f"custom code on {spec.symbol}"
