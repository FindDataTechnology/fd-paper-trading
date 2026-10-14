"""Unit tests for strategy/indicators.py against hand-computed values."""

import pytest

from fd_paper_trading.strategy import indicators


# ---- sma ----


def test_sma_basic():
    assert indicators.sma([1.0, 2.0, 3.0, 4.0], 2) == 3.5


def test_sma_uses_last_n():
    assert indicators.sma([100.0, 1.0, 2.0, 3.0], 3) == 2.0


def test_sma_insufficient_data():
    assert indicators.sma([1.0, 2.0], 3) is None
    assert indicators.sma([], 1) is None


# ---- ema ----


def test_ema_constant_series_is_constant():
    assert indicators.ema([5.0] * 10, 3) == pytest.approx(5.0)


def test_ema_known_value():
    # n=3: seed = mean(1,2,3) = 2 at idx2, k = 0.5
    # idx3: 4*0.5 + 2*0.5 = 3; idx4: 5*0.5 + 3*0.5 = 4
    assert indicators.ema([1.0, 2.0, 3.0, 4.0, 5.0], 3) == pytest.approx(4.0)


def test_ema_insufficient_data():
    assert indicators.ema([1.0, 2.0], 3) is None


# ---- rsi ----


def test_rsi_all_gains_is_100():
    assert indicators.rsi([1.0, 2.0, 3.0, 4.0, 5.0], 3) == 100.0


def test_rsi_all_losses_is_0():
    assert indicators.rsi([5.0, 4.0, 3.0, 2.0, 1.0], 3) == 0.0


def test_rsi_known_value():
    # 15 values, n=14 (seed only): 10 up-moves of 1, 4 down-moves of 1
    # rs = (10/14) / (4/14) = 2.5 -> rsi = 100 - 100/3.5
    values = [1, 2, 3, 2, 3, 4, 3, 4, 5, 4, 5, 6, 5, 6, 7]
    assert indicators.rsi([float(v) for v in values], 14) == pytest.approx(71.428571, abs=1e-4)


def test_rsi_insufficient_data():
    assert indicators.rsi([1.0, 2.0, 3.0], 14) is None


# ---- macd ----


def test_macd_constant_series_is_zero():
    m = indicators.macd([7.0] * 40, 12, 26, 9)
    assert m is not None
    line, signal, hist = m
    assert line == pytest.approx(0.0)
    assert signal == pytest.approx(0.0)
    assert hist == pytest.approx(0.0)


def test_macd_line_equals_ema_difference():
    values = [float(v) for v in range(1, 60)]
    m = indicators.macd(values, 12, 26, 9)
    assert m is not None
    line, _signal, _hist = m
    assert line == pytest.approx(
        indicators.ema(values, 12) - indicators.ema(values, 26)
    )


def test_macd_length_boundary():
    values = [float(v) for v in range(1, 40)]
    assert indicators.macd(values[:33], 12, 26, 9) is None  # slow + signal - 2
    assert indicators.macd(values[:34], 12, 26, 9) is not None  # slow + signal - 1


def test_macd_rejects_fast_ge_slow():
    assert indicators.macd([float(v) for v in range(60)], 26, 12, 9) is None


# ---- bollinger ----


def test_bollinger_constant_series_zero_width():
    mid, upper, lower = indicators.bollinger([3.0] * 20, 20, 2.0)
    assert mid == pytest.approx(3.0)
    assert upper == pytest.approx(3.0)
    assert lower == pytest.approx(3.0)


def test_bollinger_known_values():
    # 1..20: mid = 10.5, population var = (20²-1)/12 = 33.25
    values = [float(v) for v in range(1, 21)]
    mid, upper, lower = indicators.bollinger(values, 20, 2.0)
    std = 33.25**0.5
    assert mid == pytest.approx(10.5)
    assert upper == pytest.approx(10.5 + 2 * std)
    assert lower == pytest.approx(10.5 - 2 * std)


def test_bollinger_insufficient_data():
    assert indicators.bollinger([1.0, 2.0], 20, 2.0) is None
