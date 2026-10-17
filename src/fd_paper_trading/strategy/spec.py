"""Tri-track strategy spec models (design D1, spec: strategy-management).

A strategy's persisted ``spec`` (JSONB) is exactly one of:

- Template track: ``{"kind": "template", "template": <id>, "symbol": <sym>,
  "params": {...}}``
- Code track: ``{"kind": "code", "symbol": <sym>, "source": <python>}``
- Weight track (multi-symbol): ``{"kind": "weight", "universe": [<sym>...],
  "rebalance": {"every_n_bars": <n>}}`` plus exactly one of ``"source"``
  (defining ``target_weights(ctx)``) or ``"weights_series"``
  (``{<ISO date>: {<sym>: <number>}}``, where a period may also be a full
  ``TargetWeights`` contract instance as emitted by a pack).

``validate_spec`` is the single entry point — it returns a normalized spec
(template params have catalog defaults baked in, symbols uppercased) or
raises ``SpecValidationError`` with one human-readable message per violation
(structured for LLM self-repair in the conversation tools).

Weight legality is **not** decided here: every period's weights are handed to
the contract layer (``.weights``), which is the only place the "non-negative,
Σ ≤ 1, symbols within the universe" rules live. This module decides shape.
"""

from __future__ import annotations

import re
from datetime import date as _date
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator, model_validator

from .templates import TemplateValidationError, validate_template_params
from .weights import validate_contract_instance, validate_universe, validate_weights, weights_from_contract

_SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,15}")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: A weights_series period is a full contract instance when it carries all of
#: these; the names are contract-only (symbol keys are uppercased, these are
#: lowercase), so a bare period mapping cannot be misread as one.
_CONTRACT_MARKERS = ("schema_version", "weights", "producer")


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


class Rebalance(BaseModel):
    """When a weight strategy re-evaluates and trades."""

    model_config = ConfigDict(extra="forbid")

    every_n_bars: int

    @field_validator("every_n_bars")
    @classmethod
    def _positive_int(cls, v: object) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError("every_n_bars must be a positive integer")
        if v < 1:
            raise ValueError(f"every_n_bars must be a positive integer (got {v})")
        return v


class WeightSpec(BaseModel):
    """Multi-symbol track: a universe, a rebalance cadence, and one weight source.

    ``weights_series`` periods are validated through the contract layer and
    normalized to a plain ``{symbol: weight}`` mapping, matching the shape the
    spec declares. A period given as a full ``TargetWeights`` contract instance
    keeps its ``cash_floor`` — recorded per period in ``cash_floors`` rather than
    smuggled into the symbol mapping, where it would collide with a real symbol
    named ``CASH``. ``source`` is AST-checked like the code track, expecting
    ``target_weights(ctx)``.
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["weight"]
    universe: list[str]
    rebalance: Rebalance
    source: str | None = None
    #: Deliberately loose in the annotation: a period is either a bare
    #: ``{symbol: weight}`` mapping or a full TargetWeights instance, and only
    #: the contract layer may judge weight legality. A strict ``dict[str, float]``
    #: here would reject instances before the normalizer ever sees them, moving
    #: the verdict away from the contract.
    weights_series: dict[str, dict[str, Any]] | None = None
    #: Per-period target cash floor, present only where a contract instance
    #: supplied one. A period without an entry means "cash is the residual".
    cash_floors: dict[str, float] = Field(default_factory=dict)

    @field_validator("universe")
    @classmethod
    def _universe_ok(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            raise ValueError("universe must be a list of symbols")
        cleaned = [_clean_symbol(item) for item in v]
        errors = validate_universe(cleaned)
        if errors:
            raise ValueError("; ".join(errors))
        return cleaned

    @model_validator(mode="after")
    def _one_source_only(self) -> "WeightSpec":
        has_source = bool(self.source and self.source.strip())
        has_series = self.weights_series is not None
        if has_source and has_series:
            raise ValueError("exactly one of source / weights_series is required, got both")
        if not has_source and not has_series:
            raise ValueError("exactly one of source / weights_series is required, got neither")
        if self.weights_series is not None:
            series, floors = _normalize_weights_series(self.universe, self.weights_series)
            self.weights_series = series
            self.cash_floors = floors
        return self


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        return False
    try:
        _date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _looks_like_contract(period: object) -> bool:
    return isinstance(period, dict) and all(k in period for k in _CONTRACT_MARKERS)


def _normalize_period(
    universe: list[str], period_key: str, period: object
) -> tuple[dict[str, float], float]:
    """One period → ``(validated mapping, cash floor)``, or ``ValueError`` naming it.

    Two accepted forms: a bare ``{symbol: weight}`` mapping, or a full
    ``TargetWeights`` contract instance (a pack's published output). Both end up
    judged by the contract layer — the instance through ``weights_from_contract``
    (which keeps its own ``cash_floor`` and validates the envelope), the bare
    mapping through ``validate_weights`` with an engine-built envelope.
    """
    if not isinstance(period, dict):
        raise ValueError(f"weights_series['{period_key}'] must be an object")
    if _looks_like_contract(period):
        errors = validate_contract_instance(period)
        if errors:
            raise ValueError(
                f"weights_series['{period_key}'] is an invalid TargetWeights "
                f"instance: " + "; ".join(errors)
            )
        as_of, weights, cash_floor = weights_from_contract(period)
        if as_of != period_key:
            raise ValueError(
                f"weights_series['{period_key}']: instance as_of is {as_of!r}; "
                f"the period key and as_of must agree"
            )
        outside = sorted(set(weights) - set(universe))
        if outside:
            raise ValueError(
                f"weights_series['{period_key}']: symbols not in universe: "
                + ", ".join(outside)
            )
        return {str(k).upper(): float(v) for k, v in weights.items()}, cash_floor
    normalized = {_clean_symbol(k): v for k, v in period.items()}
    # The contract judges legality; a bare period carries no cash floor of its
    # own (the residual beyond Σweights is cash by construction).
    errors = validate_weights(universe, normalized)
    if errors:
        raise ValueError(f"weights_series['{period_key}']: " + "; ".join(errors))
    return normalized, 0.0


def _normalize_weights_series(
    universe: list[str], series: object
) -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    """Validate every period and the series' own shape (keys are ISO dates)."""
    if not isinstance(series, dict):
        raise ValueError("weights_series must be an object of {date: weights}")
    if not series:
        raise ValueError("weights_series must contain at least one period")
    out: dict[str, dict[str, float]] = {}
    floors: dict[str, float] = {}
    for key, period in series.items():
        if not _is_iso_date(key):
            raise ValueError(
                f"weights_series key {key!r} must be an ISO date (YYYY-MM-DD)"
            )
        weights, cash_floor = _normalize_period(universe, key, period)
        out[key] = weights
        if cash_floor > 0:
            floors[key] = cash_floor
    return out, floors


StrategySpec = Annotated[TemplateSpec | CodeSpec | WeightSpec, Field(discriminator="kind")]
_adapter = TypeAdapter(StrategySpec)


def validate_spec(raw: object) -> TemplateSpec | CodeSpec | WeightSpec:
    """Validate a raw spec dict; return the normalized typed spec.

    Raises ``SpecValidationError`` listing every violation. Code-track and
    weight-track sources are also AST-validated here (design D6) so create/update,
    backtest, and the live runner all reject unsafe source at the same chokepoint.
    """
    if not isinstance(raw, dict):
        raise SpecValidationError(["spec must be an object"])
    try:
        spec = _adapter.validate_python(raw)
    except ValidationError as exc:
        raise SpecValidationError([_format_error(e) for e in exc.errors()]) from exc
    source = getattr(spec, "source", None)
    if source is not None:
        from .codegen import validate_source

        entry_point = "target_weights" if isinstance(spec, WeightSpec) else "on_bar"
        errors = validate_source(source, entry_point=entry_point)
        if errors:
            raise SpecValidationError(errors)
    return spec


def _format_error(err: dict) -> str:
    parts = list(err.get("loc", ()))
    # Discriminated-union errors are prefixed with the variant tag; drop it.
    if parts and parts[0] in ("template", "code", "weight"):
        parts = parts[1:]
    loc = ".".join(str(part) for part in parts)
    msg = err.get("msg", "invalid")
    return f"{loc}: {msg}" if loc else msg


def spec_summary(spec: TemplateSpec | CodeSpec | WeightSpec) -> str:
    """One-line human summary for lists/cards."""
    if isinstance(spec, TemplateSpec):
        params = ", ".join(f"{k}={v}" for k, v in sorted(spec.params.items()))
        return f"{spec.template}({params}) on {spec.symbol}"
    if isinstance(spec, WeightSpec):
        n = len(spec.universe)
        source = "code" if spec.source else f"{len(spec.weights_series or {})} periods"
        return (
            f"weights {source} over {n} symbol(s), "
            f"rebalance every {spec.rebalance.every_n_bars} bars"
        )
    return f"custom code on {spec.symbol}"
