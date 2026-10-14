"""Unit tests for strategy/templates.py (param validation + entry/exit
behavior on synthetic bars) and the StrategyContext contract."""

from decimal import Decimal

import pytest

from fd_trade.marketdata.provider import Bar
from fd_trade.strategy import (
    CATALOG,
    StrategyContext,
    TemplateValidationError,
    list_templates,
    make_evaluator,
    validate_template_params,
)


def bars_from_closes(closes: list[float]) -> list[Bar]:
    """OHLC collapsed to the close (fd-open-data daily shape)."""
    return [
        Bar(
            ts=1_700_000_000 + i * 86_400,
            open=Decimal(str(c)),
            high=Decimal(str(c)),
            low=Decimal(str(c)),
            close=Decimal(str(c)),
            volume=Decimal(0),
        )
        for i, c in enumerate(closes)
    ]


def drive(evaluator, closes: list[float]) -> list[tuple[int, str, Decimal]]:
    """Replay a series bar-by-bar, filling signals immediately (position only).

    Returns [(bar_index, side, quantity)] for every emitted signal.
    """
    bars_all = bars_from_closes(closes)
    pos = Decimal(0)
    events: list[tuple[int, str, Decimal]] = []
    for i in range(len(bars_all)):
        ctx = StrategyContext(bars_all[: i + 1], position=pos, cash=Decimal("100000"))
        evaluator.on_bar(ctx)
        for sig in ctx.signals:
            events.append((i, sig.side, sig.quantity))
            pos += sig.quantity if sig.side == "BUY" else -sig.quantity
    return events


# ---- catalog & param validation ----


def test_catalog_has_five_templates():
    assert sorted(CATALOG) == [
        "bollinger_breakout",
        "dca",
        "macd_cross",
        "rsi_reversion",
        "sma_cross",
    ]
    listed = list_templates()
    assert {t["id"] for t in listed} == set(CATALOG)
    for t in listed:
        assert t["name"] and t["description"] and t["params_schema"]["properties"]


def test_defaults_baked_in():
    clean = validate_template_params("sma_cross", {"quantity": 10})
    assert clean == {"fast": 10, "slow": 30, "quantity": 10}


def test_integral_float_accepted_as_integer():
    clean = validate_template_params("sma_cross", {"quantity": 10.0, "fast": 5.0})
    assert clean["quantity"] == 10
    assert clean["fast"] == 5


@pytest.mark.parametrize(
    ("template", "params", "needle"),
    [
        ("sma_cross", {"quantity": 10, "fast": -3}, "'fast' must be >= 1"),
        ("sma_cross", {"fast": 5}, "missing required parameter 'quantity'"),
        ("sma_cross", {"quantity": 10, "bogus": 1}, "unknown parameter 'bogus'"),
        ("sma_cross", {"quantity": 10, "fast": 30, "slow": 10}, "must be less than"),
        ("sma_cross", {"quantity": True}, "must be an integer"),
        (
            "rsi_reversion",
            {"quantity": 5, "oversold": 70, "overbought": 30},
            "must be less than",
        ),
        ("bollinger_breakout", {"quantity": 5, "std_mult": 0}, "must be >="),
    ],
)
def test_invalid_params_rejected(template, params, needle):
    with pytest.raises(TemplateValidationError) as exc_info:
        validate_template_params(template, params)
    assert any(needle in e for e in exc_info.value.errors)


def test_unknown_template_lists_valid_ids():
    with pytest.raises(TemplateValidationError) as exc_info:
        validate_template_params("nope", {})
    msg = exc_info.value.errors[0]
    assert "unknown template 'nope'" in msg
    assert "sma_cross" in msg and "dca" in msg


# ---- entry/exit behavior on synthetic bars ----


def test_sma_cross_buys_on_cross_up_and_sells_all_on_cross_down():
    evaluator = make_evaluator(
        "sma_cross", {"fast": 2, "slow": 3, "quantity": 10}
    )
    events = drive(evaluator, [5, 5, 5, 4, 4, 6, 7, 6, 5])
    assert events == [(5, "BUY", Decimal(10)), (8, "SELL", Decimal(10))]


def test_cross_template_emits_nothing_on_monotone_uptrend():
    evaluator = make_evaluator(
        "sma_cross", {"fast": 2, "slow": 3, "quantity": 10}
    )
    assert drive(evaluator, [1, 2, 3, 4, 5, 6, 7]) == []


def test_rsi_reversion_buys_oversold_sells_overbought_and_guards_position():
    evaluator = make_evaluator(
        "rsi_reversion",
        {"period": 3, "oversold": 30, "overbought": 70, "quantity": 5},
    )
    events = drive(evaluator, [10, 9, 8, 7, 6, 7, 8, 9, 10])
    # buy at bar 3 (rsi 0), guard blocks the repeat buy at bar 4 while long,
    # sell at bar 7 when Wilder-smoothed rsi clears 70
    assert events == [(3, "BUY", Decimal(5)), (7, "SELL", Decimal(5))]


def test_bollinger_breakout_buys_above_upper_sells_below_mid():
    evaluator = make_evaluator(
        "bollinger_breakout", {"period": 3, "std_mult": 1.0, "quantity": 5}
    )
    events = drive(evaluator, [5, 5, 5, 5, 5, 10, 5, 5])
    assert events == [(5, "BUY", Decimal(5)), (6, "SELL", Decimal(5))]


def test_macd_cross_buys_and_sells_on_line_crosses():
    evaluator = make_evaluator(
        "macd_cross", {"fast": 2, "slow": 4, "signal": 2, "quantity": 3}
    )
    events = drive(evaluator, [10, 9, 8, 9, 8, 10, 8, 7])
    assert events == [(5, "BUY", Decimal(3)), (6, "SELL", Decimal(3))]


def test_dca_buys_every_interval_and_never_sells():
    evaluator = make_evaluator("dca", {"interval_bars": 3, "quantity": 7})
    events = drive(evaluator, [5, 5, 5, 5, 5, 5, 5])
    assert events == [(2, "BUY", Decimal(7)), (5, "BUY", Decimal(7))]


# ---- StrategyContext contract ----


def test_ctx_history_is_oldest_first_ending_at_current_bar():
    bars = bars_from_closes([1, 2, 3])
    ctx = StrategyContext(bars, position=Decimal(0), cash=Decimal(100))
    assert ctx.closes() == [1.0, 2.0, 3.0]
    assert ctx.opens() == [1.0, 2.0, 3.0]
    assert ctx.highs()[-1] == 3.0
    assert ctx.lows()[0] == 1.0
    assert ctx.volumes() == [0.0, 0.0, 0.0]


def test_ctx_exposes_position_and_cash():
    ctx = StrategyContext(bars_from_closes([1]), position=Decimal(7), cash=Decimal(55))
    assert ctx.position == Decimal(7)
    assert ctx.cash == Decimal(55)


def test_ctx_indicators_match_indicator_functions():
    ctx = StrategyContext(
        bars_from_closes([1, 2, 3, 4, 5]), position=Decimal(0), cash=Decimal(0)
    )
    assert ctx.sma(2) == 4.5
    assert ctx.rsi(3) == 100.0
    assert ctx.bollinger(3, 2.0)[0] == pytest.approx(4.0)


def test_ctx_buy_sell_emit_signals_with_decimal_quantity():
    ctx = StrategyContext(bars_from_closes([1]), position=Decimal(0), cash=Decimal(0))
    ctx.buy(10)
    ctx.sell(2.5, limit_price="101.25")
    buy, sell = ctx.signals
    assert (buy.side, buy.quantity, buy.limit_price) == ("BUY", Decimal(10), None)
    assert sell.side == "SELL"
    assert sell.quantity == Decimal("2.5")
    assert sell.limit_price == Decimal("101.25")


def test_ctx_rejects_non_positive_quantity_and_limit():
    ctx = StrategyContext(bars_from_closes([1]), position=Decimal(0), cash=Decimal(0))
    with pytest.raises(ValueError):
        ctx.buy(0)
    with pytest.raises(ValueError):
        ctx.sell(-1)
    with pytest.raises(ValueError):
        ctx.buy(1, limit_price=-5)


def test_ctx_requires_at_least_one_bar():
    with pytest.raises(ValueError):
        StrategyContext([], position=Decimal(0), cash=Decimal(0))
