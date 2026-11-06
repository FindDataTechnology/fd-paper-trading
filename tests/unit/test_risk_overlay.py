"""Unit tests for the run-level risk overlay (spec: strategy-live-execution
"Run-level risk overlay shared by backtest and live" + strategy-backtest
"回测接受 run 级风控覆盖").

Pure-module tests: no engines, no ledger — the overlay returns data and the
engines apply it. The parity guarantee ("same logic in both engines") is
structural: both tests drive the same functions.
"""

from decimal import Decimal

import pytest

from fd_paper_trading.strategy.risk_overlay import (
    CooldownState,
    OverlayConfigError,
    RiskConfig,
    effective_target_weight,
    evaluate_bar_overlay,
    evaluate_quote_overlay,
)


# ---- config validation ----

def test_risk_config_empty_is_inactive():
    cfg = RiskConfig.from_dict(None)
    assert cfg.active is False
    assert cfg.to_dict() == {}


def test_risk_config_rejects_out_of_range():
    with pytest.raises(OverlayConfigError):
        RiskConfig.from_dict({"stop_loss_pct": "1.5"})
    with pytest.raises(OverlayConfigError):
        RiskConfig.from_dict({"trailing_stop_pct": "0"})
    with pytest.raises(OverlayConfigError):
        RiskConfig.from_dict({"cooldown_bars": -1})


def test_risk_config_roundtrip():
    cfg = RiskConfig.from_dict({"stop_loss_pct": 0.05, "trailing_stop_pct": 0.1,
                                "cooldown_bars": 2})
    assert cfg.active is True
    assert cfg.to_dict() == {"stop_loss_pct": 0.05, "trailing_stop_pct": 0.1,
                             "cooldown_bars": 2}


# ---- backtest-side bar overlay ----

def test_bar_stop_loss_triggers_on_low():
    cfg = RiskConfig(stop_loss_pct=Decimal("0.05"))
    t = evaluate_bar_overlay(
        cfg, "600036", "SELL", entry_price=Decimal("100"),
        peak_value=None,
        bar_open=Decimal("96"), bar_close=Decimal("95.5"),
        bar_low=Decimal("94.8"), bar_high=Decimal("97"),
    )
    assert t is not None and t.kind == "stop_loss"
    # stop level = 95; low 94.8 reached it
    assert t.trigger_level == Decimal("95.000")


def test_bar_gap_down_fills_at_open_not_at_stop():
    """The spec's headline scenario: entry 100, stop 5%, next bar opens 90
    with low 89 -> fires on that bar and fills at 90 (the open, worse than
    the stop level 95), never at 95."""
    cfg = RiskConfig(stop_loss_pct=Decimal("0.05"))
    t = evaluate_bar_overlay(
        cfg, "600036", "SELL", entry_price=Decimal("100"),
        peak_value=None,
        bar_open=Decimal("90"), bar_close=Decimal("89.5"),
        bar_low=Decimal("89"), bar_high=Decimal("91"),
    )
    assert t is not None
    assert t.trigger_level == Decimal("95.000")
    assert t.fill_price == Decimal("90")  # the open — conservative


def test_bar_no_trigger_when_low_stays_above_level():
    cfg = RiskConfig(stop_loss_pct=Decimal("0.05"))
    t = evaluate_bar_overlay(
        cfg, "600036", "SELL", entry_price=Decimal("100"),
        peak_value=None,
        bar_open=Decimal("100"), bar_close=Decimal("100.5"),
        bar_low=Decimal("99"), bar_high=Decimal("101"),
    )
    assert t is None  # low 99 > stop level 95


def test_bar_trailing_stop_uses_running_peak():
    cfg = RiskConfig(trailing_stop_pct=Decimal("0.10"))
    # peak 120; low 107 <= 108 (120 * 0.9) -> trailing fires
    t = evaluate_bar_overlay(
        cfg, "600036", "SELL", entry_price=Decimal("100"),
        peak_value=Decimal("120"),
        bar_open=Decimal("108"), bar_close=Decimal("107.5"),
        bar_low=Decimal("107"), bar_high=Decimal("110"),
    )
    assert t is not None and t.kind == "trailing_stop"
    assert t.trigger_level == Decimal("108.00")
    # peak 120; low 109 > 108 -> no trigger
    t2 = evaluate_bar_overlay(
        cfg, "600036", "SELL", entry_price=Decimal("100"),
        peak_value=Decimal("120"),
        bar_open=Decimal("110"), bar_close=Decimal("109.5"),
        bar_low=Decimal("109"), bar_high=Decimal("111"),
    )
    assert t2 is None


# ---- live-side quote overlay ----

def test_quote_stop_loss_triggers_on_bid():
    cfg = RiskConfig(stop_loss_pct=Decimal("0.05"))
    t = evaluate_quote_overlay(
        cfg, "600036", "SELL", entry_price=Decimal("100"),
        peak_value=None,
        quote_bid=Decimal("94"), quote_ask=Decimal("95"),
    )
    assert t is not None and t.kind == "stop_loss"
    assert t.fill_price == Decimal("94")  # the achievable exit (bid)


def test_quote_no_trigger_when_bid_above_level():
    cfg = RiskConfig(stop_loss_pct=Decimal("0.05"))
    t = evaluate_quote_overlay(
        cfg, "600036", "SELL", entry_price=Decimal("100"),
        peak_value=None,
        quote_bid=Decimal("96"), quote_ask=Decimal("97"),
    )
    assert t is None


# ---- parity: bar and quote agree at the same price point ----

def test_bar_and_quote_parity_at_same_price():
    """The parity guarantee: same entry, same level, the two engines fire
    together. tick=bar replay protocol (task 4.1) rests on this."""
    cfg = RiskConfig(stop_loss_pct=Decimal("0.05"))
    bar_t = evaluate_bar_overlay(
        cfg, "X", "SELL", entry_price=Decimal("100"), peak_value=None,
        bar_open=Decimal("95"), bar_close=Decimal("95"),
        bar_low=Decimal("95"), bar_high=Decimal("95"),
    )
    quote_t = evaluate_quote_overlay(
        cfg, "X", "SELL", entry_price=Decimal("100"), peak_value=None,
        quote_bid=Decimal("95"), quote_ask=Decimal("95.02"),
    )
    assert (bar_t is None) == (quote_t is None) == False
    assert bar_t.trigger_level == quote_t.trigger_level == Decimal("95.000")


# ---- cooldown ----

def test_cooldown_zeroes_effective_weight_then_resumes():
    cd = CooldownState()
    cd.arm("600036", 2)
    assert effective_target_weight("600036", Decimal("0.5"), cd) == Decimal("0")
    cd.tick()
    assert effective_target_weight("600036", Decimal("0.5"), cd) == Decimal("0")
    cd.tick()
    assert effective_target_weight("600036", Decimal("0.5"), cd) == Decimal("0.5")


def test_cooldown_tick_is_scoped():
    cd = CooldownState()
    cd.arm("A", 2)
    cd.arm("B", 2)
    cd.tick(["A"])
    assert cd.suppressed("A") and cd.suppressed("B")
    cd.tick(["A"])
    assert not cd.suppressed("A") and cd.suppressed("B")


def test_overlay_never_opens_positions():
    """Structural assertion: the module's only outputs are triggers with a
    close-out price; there is no API that returns an entry instruction."""
    import inspect

    from fd_paper_trading.strategy import risk_overlay as mod

    public = [n for n, _ in inspect.getmembers(mod, inspect.isfunction)
              if not n.startswith("_") and n not in ("dataclass", "field")]
    assert set(public) <= {
        "evaluate_bar_overlay", "evaluate_quote_overlay",
        "effective_target_weight", "_close_price_for_bar",
    }