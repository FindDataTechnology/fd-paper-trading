"""Built-in strategy template catalog + interpreter (design D11).

Templates are single-symbol, long-only, fixed-quantity strategies evaluated
by an interpreter that drives the same ``StrategyContext`` custom code uses —
never generated code (spec: "Template strategies SHALL execute as interpreted
parameter logic"). One evaluator instance per run holds any cross-bar state
(previous indicator values for cross detection, bar counters for DCA).

Exit semantics: an exit signal closes the entire position (``ctx.position``).
Entries are guarded — a template only enters when flat (except ``dca``, which
accumulates every ``interval_bars`` bars and never exits).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .ctx import StrategyContext


class TemplateValidationError(ValueError):
    """Param/template validation failure with one message per violation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


# --------------------------------------------------------------------------
# Param validation (mini JSON-Schema subset: type/minimum/maximum/default/
# required/additionalProperties — enough for the catalog's own schemas)
# --------------------------------------------------------------------------


def _check_type(name: str, value: object, schema: dict) -> tuple[object, str | None]:
    t = schema.get("type")
    if t == "integer":
        if isinstance(value, bool):
            return value, f"'{name}' must be an integer"
        if isinstance(value, int):
            return value, None
        if isinstance(value, float) and value.is_integer():
            return int(value), None
        return value, f"'{name}' must be an integer, got {value!r}"
    if t == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return value, f"'{name}' must be a number, got {value!r}"
        return value, None
    if t == "string":
        if not isinstance(value, str) or not value.strip():
            return value, f"'{name}' must be a non-empty string"
        return value, None
    if t == "boolean":
        if not isinstance(value, bool):
            return value, f"'{name}' must be a boolean"
        return value, None
    return value, None


def validate_params(schema: dict, params: dict) -> tuple[dict, list[str]]:
    """Validate ``params`` against a catalog schema; apply defaults.

    Returns ``(clean_params, errors)`` — clean params only when errors is
    empty. Unknown keys are rejected (``additionalProperties: false``).
    """
    if not isinstance(params, dict):
        return {}, ["params must be an object"]
    props: dict = schema.get("properties", {})
    required: list[str] = schema.get("required", [])
    errors: list[str] = []
    clean: dict = {}

    for key in params:
        if key not in props:
            errors.append(f"unknown parameter '{key}' (valid: {', '.join(sorted(props))})")
    for name, pspec in props.items():
        if name in params:
            value, err = _check_type(name, params[name], pspec)
            if err:
                errors.append(err)
                continue
            if "minimum" in pspec and isinstance(value, (int, float)) and value < pspec["minimum"]:
                errors.append(f"'{name}' must be >= {pspec['minimum']}, got {value}")
                continue
            if "maximum" in pspec and isinstance(value, (int, float)) and value > pspec["maximum"]:
                errors.append(f"'{name}' must be <= {pspec['maximum']}, got {value}")
                continue
            clean[name] = value
        elif name in required:
            errors.append(f"missing required parameter '{name}'")
        elif "default" in pspec:
            clean[name] = pspec["default"]
    return clean, errors


# --------------------------------------------------------------------------
# Evaluators (one instance per run — holds cross-bar state)
# --------------------------------------------------------------------------


class _CrossEvaluator:
    """Base for cross templates: entry on cross-above, exit on cross-below."""

    def __init__(self, params: dict) -> None:
        self.quantity = params["quantity"]
        self._prev: tuple[float, float] | None = None

    def _pair(self, ctx: StrategyContext) -> tuple[float, float] | None:
        raise NotImplementedError

    def on_bar(self, ctx: StrategyContext) -> None:
        pair = self._pair(ctx)
        if pair is None:
            return
        prev, self._prev = self._prev, pair
        if prev is None:
            return
        (pf, ps), (cf, cs) = prev, pair
        if pf <= ps and cf > cs and ctx.position == 0:
            ctx.buy(self.quantity)
        elif pf >= ps and cf < cs and ctx.position > 0:
            ctx.sell(ctx.position)


class SmaCrossEvaluator(_CrossEvaluator):
    def __init__(self, params: dict) -> None:
        super().__init__(params)
        self.fast = params["fast"]
        self.slow = params["slow"]

    def _pair(self, ctx: StrategyContext) -> tuple[float, float] | None:
        fast, slow = ctx.sma(self.fast), ctx.sma(self.slow)
        if fast is None or slow is None:
            return None
        return fast, slow


class MacdCrossEvaluator(_CrossEvaluator):
    def __init__(self, params: dict) -> None:
        super().__init__(params)
        self.fast = params["fast"]
        self.slow = params["slow"]
        self.signal = params["signal"]

    def _pair(self, ctx: StrategyContext) -> tuple[float, float] | None:
        m = ctx.macd(self.fast, self.slow, self.signal)
        if m is None:
            return None
        line, signal, _hist = m
        return line, signal


class RsiReversionEvaluator:
    def __init__(self, params: dict) -> None:
        self.period = params["period"]
        self.oversold = params["oversold"]
        self.overbought = params["overbought"]
        self.quantity = params["quantity"]

    def on_bar(self, ctx: StrategyContext) -> None:
        r = ctx.rsi(self.period)
        if r is None:
            return
        if r < self.oversold and ctx.position == 0:
            ctx.buy(self.quantity)
        elif r > self.overbought and ctx.position > 0:
            ctx.sell(ctx.position)


class BollingerBreakoutEvaluator:
    def __init__(self, params: dict) -> None:
        self.period = params["period"]
        self.std_mult = params["std_mult"]
        self.quantity = params["quantity"]

    def on_bar(self, ctx: StrategyContext) -> None:
        bands = ctx.bollinger(self.period, self.std_mult)
        if bands is None:
            return
        mid, upper, _lower = bands
        close = ctx.closes()[-1]
        if close > upper and ctx.position == 0:
            ctx.buy(self.quantity)
        elif close < mid and ctx.position > 0:
            ctx.sell(ctx.position)


class DcaEvaluator:
    def __init__(self, params: dict) -> None:
        self.interval_bars = params["interval_bars"]
        self.quantity = params["quantity"]
        self._bars_seen = 0

    def on_bar(self, ctx: StrategyContext) -> None:
        self._bars_seen += 1
        if self._bars_seen % self.interval_bars == 0:
            ctx.buy(self.quantity)


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateDef:
    id: str
    name: str
    description: str
    params_schema: dict
    evaluator_cls: type
    extra_check: Callable[[dict], list[str]] | None = None


def _fast_slow_check(params: dict) -> list[str]:
    if params.get("fast", 0) >= params.get("slow", 0):
        return [f"'fast' ({params['fast']}) must be less than 'slow' ({params['slow']})"]
    return []


def _oversold_overbought_check(params: dict) -> list[str]:
    if params.get("oversold", 0) >= params.get("overbought", 0):
        return [
            f"'oversold' ({params['oversold']}) must be less than 'overbought' ({params['overbought']})"
        ]
    return []


_QUANTITY = {"type": "integer", "minimum": 1, "description": "Shares per trade"}


CATALOG: dict[str, TemplateDef] = {
    "sma_cross": TemplateDef(
        id="sma_cross",
        name="SMA crossover",
        description=(
            "Buy when the fast SMA crosses above the slow SMA; close the "
            "position when it crosses back below."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "fast": {"type": "integer", "minimum": 1, "default": 10,
                         "description": "Fast SMA period (bars)"},
                "slow": {"type": "integer", "minimum": 2, "default": 30,
                         "description": "Slow SMA period (bars)"},
                "quantity": _QUANTITY,
            },
            "required": ["quantity"],
            "additionalProperties": False,
        },
        evaluator_cls=SmaCrossEvaluator,
        extra_check=_fast_slow_check,
    ),
    "rsi_reversion": TemplateDef(
        id="rsi_reversion",
        name="RSI mean reversion",
        description=(
            "Buy when RSI drops below the oversold level; close the position "
            "when RSI rises above the overbought level."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "period": {"type": "integer", "minimum": 2, "default": 14,
                           "description": "RSI period (bars)"},
                "oversold": {"type": "number", "minimum": 1, "maximum": 99, "default": 30,
                             "description": "Buy below this RSI"},
                "overbought": {"type": "number", "minimum": 1, "maximum": 99, "default": 70,
                               "description": "Sell above this RSI"},
                "quantity": _QUANTITY,
            },
            "required": ["quantity"],
            "additionalProperties": False,
        },
        evaluator_cls=RsiReversionEvaluator,
        extra_check=_oversold_overbought_check,
    ),
    "bollinger_breakout": TemplateDef(
        id="bollinger_breakout",
        name="Bollinger breakout",
        description=(
            "Buy when the close breaks above the upper Bollinger band; close "
            "the position when it falls back below the middle band."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "period": {"type": "integer", "minimum": 2, "default": 20,
                           "description": "Band window (bars)"},
                "std_mult": {"type": "number", "minimum": 0.1, "maximum": 10, "default": 2.0,
                             "description": "Band width in standard deviations"},
                "quantity": _QUANTITY,
            },
            "required": ["quantity"],
            "additionalProperties": False,
        },
        evaluator_cls=BollingerBreakoutEvaluator,
    ),
    "macd_cross": TemplateDef(
        id="macd_cross",
        name="MACD crossover",
        description=(
            "Buy when the MACD line crosses above its signal line; close the "
            "position when it crosses back below."
        ),
        params_schema={
            "type": "object",
            "properties": {
                "fast": {"type": "integer", "minimum": 1, "default": 12,
                         "description": "Fast EMA period"},
                "slow": {"type": "integer", "minimum": 2, "default": 26,
                         "description": "Slow EMA period"},
                "signal": {"type": "integer", "minimum": 1, "default": 9,
                           "description": "Signal EMA period"},
                "quantity": _QUANTITY,
            },
            "required": ["quantity"],
            "additionalProperties": False,
        },
        evaluator_cls=MacdCrossEvaluator,
        extra_check=_fast_slow_check,
    ),
    "dca": TemplateDef(
        id="dca",
        name="Dollar-cost averaging",
        description="Buy a fixed quantity every N bars; never sells.",
        params_schema={
            "type": "object",
            "properties": {
                "interval_bars": {"type": "integer", "minimum": 1, "default": 5,
                                  "description": "Buy every N bars"},
                "quantity": _QUANTITY,
            },
            "required": ["quantity"],
            "additionalProperties": False,
        },
        evaluator_cls=DcaEvaluator,
    ),
}


def list_templates() -> list[dict]:
    """Catalog entries for the API/MCP tools (id, description, JSON Schema)."""
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "params_schema": t.params_schema,
        }
        for t in CATALOG.values()
    ]


def validate_template_params(template_id: str, params: dict) -> dict:
    """Validate params for a template; return clean params with defaults.

    Raises ``TemplateValidationError`` listing every violation (unknown
    template ids list the valid ones — LLM self-repair friendly).
    """
    tdef = CATALOG.get(template_id)
    if tdef is None:
        raise TemplateValidationError(
            [f"unknown template '{template_id}' (valid: {', '.join(sorted(CATALOG))})"]
        )
    clean, errors = validate_params(tdef.params_schema, params or {})
    if not errors and tdef.extra_check is not None:
        errors = tdef.extra_check(clean)
    if errors:
        raise TemplateValidationError(errors)
    return clean


def make_evaluator(template_id: str, params: dict):
    """A fresh evaluator for one run. Params must already be validated."""
    return CATALOG[template_id].evaluator_cls(params)
