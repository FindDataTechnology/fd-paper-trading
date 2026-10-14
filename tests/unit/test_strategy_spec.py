"""Unit tests for strategy/spec.py — dual-track spec validation."""

import pytest

from fd_trade.strategy import (
    CodeSpec,
    SpecValidationError,
    TemplateSpec,
    spec_summary,
    validate_spec,
)


def test_template_spec_normalized():
    spec = validate_spec(
        {"kind": "template", "template": "sma_cross", "symbol": "aapl",
         "params": {"quantity": 10}}
    )
    assert isinstance(spec, TemplateSpec)
    assert spec.symbol == "AAPL"  # uppercased
    assert spec.params == {"fast": 10, "slow": 30, "quantity": 10}  # defaults baked


def test_code_spec_valid():
    spec = validate_spec(
        {"kind": "code", "symbol": "MSFT", "source": "def on_bar(ctx): pass"}
    )
    assert isinstance(spec, CodeSpec)
    assert spec.symbol == "MSFT"


@pytest.mark.parametrize(
    ("raw", "needle"),
    [
        # bad symbol
        ({"kind": "template", "template": "sma_cross", "symbol": "",
          "params": {"quantity": 1}}, "symbol"),
        ({"kind": "template", "template": "sma_cross", "symbol": "aa!!",
          "params": {"quantity": 1}}, "symbol"),
        # bad template params
        ({"kind": "template", "template": "sma_cross", "symbol": "AAPL",
          "params": {"quantity": 1, "fast": -3}}, "'fast' must be >= 1"),
        # unknown template id lists valid ones
        ({"kind": "template", "template": "nope", "symbol": "AAPL",
          "params": {}}, "unknown template"),
        # unknown kind
        ({"kind": "weird", "symbol": "AAPL"}, "template"),
        # missing kind
        ({"symbol": "AAPL"}, "kind"),
        # extra key rejected
        ({"kind": "code", "symbol": "AAPL", "source": "x = 1", "evil": 1},
         "evil"),
        # blank source rejected
        ({"kind": "code", "symbol": "AAPL", "source": "   "}, "source"),
        # params not an object
        ({"kind": "template", "template": "dca", "symbol": "AAPL",
          "params": [1, 2]}, "params"),
    ],
)
def test_invalid_specs_raise_with_named_errors(raw, needle):
    with pytest.raises(SpecValidationError) as exc_info:
        validate_spec(raw)
    assert exc_info.value.errors
    assert any(needle in e for e in exc_info.value.errors)


def test_spec_must_be_object():
    with pytest.raises(SpecValidationError):
        validate_spec([1, 2, 3])


def test_spec_summary():
    template = validate_spec(
        {"kind": "template", "template": "dca", "symbol": "VTI",
         "params": {"quantity": 3, "interval_bars": 5}}
    )
    assert spec_summary(template) == "dca(interval_bars=5, quantity=3) on VTI"
    code = validate_spec({"kind": "code", "symbol": "MSFT", "source": "def on_bar(ctx):\n    pass"})
    assert spec_summary(code) == "custom code on MSFT"
