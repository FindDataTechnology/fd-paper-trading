"""Pure-Python indicators over oldest-first series.

One implementation shared by the template interpreter and the custom-code
``ctx`` (design D11) so both tracks always agree. All functions take an
oldest-first sequence of floats and return the value at the *last* bar, or
``None`` when the series is too short for the requested window.
"""

from __future__ import annotations

from typing import Sequence


def sma(values: Sequence[float], n: int) -> float | None:
    """Simple moving average of the last ``n`` values."""
    if n < 1 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def _ema_series(values: Sequence[float], n: int) -> list[float | None]:
    """Full EMA series (Wilder-style seed: SMA of the first ``n`` values).

    Entry i is the EMA ending at bar i; entries before index ``n - 1`` are
    None. Standard k = 2 / (n + 1) smoothing.
    """
    out: list[float | None] = [None] * len(values)
    if n < 1 or len(values) < n:
        return out
    k = 2.0 / (n + 1)
    ema = sum(values[:n]) / n
    out[n - 1] = ema
    for i in range(n, len(values)):
        ema = values[i] * k + ema * (1 - k)
        out[i] = ema
    return out


def ema(values: Sequence[float], n: int) -> float | None:
    """Exponential moving average of the last ``n`` values (SMA-seeded)."""
    return _ema_series(values, n)[-1]


def rsi(values: Sequence[float], n: int = 14) -> float | None:
    """Wilder's RSI over the last ``n`` periods (needs ``n + 1`` values)."""
    if n < 1 or len(values) < n + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        change = values[i] - values[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    avg_gain = gains / n
    avg_loss = losses / n
    for i in range(n + 1, len(values)):
        change = values[i] - values[i - 1]
        gain = change if change > 0 else 0.0
        loss = -change if change < 0 else 0.0
        avg_gain = (avg_gain * (n - 1) + gain) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def macd(
    values: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[float, float, float] | None:
    """(macd_line, signal_line, histogram) at the last bar.

    The signal line is an EMA over the MACD line, seeded with the SMA of the
    first ``signal`` defined MACD values (i.e. starting at bar
    ``slow + signal - 2``). Returns None when the series is too short.
    """
    if not (1 <= fast < slow) or signal < 1 or len(values) < slow + signal - 1:
        return None
    fast_series = _ema_series(values, fast)
    slow_series = _ema_series(values, slow)
    macd_series: list[float] = []
    for f, s in zip(fast_series, slow_series):
        macd_series.append((f - s) if (f is not None and s is not None) else float("nan"))
    defined = macd_series[slow - 1 :]
    signal_series = _ema_series(defined, signal)
    sig = signal_series[-1]
    if sig is None:
        return None
    line = macd_series[-1]
    return line, sig, line - sig


def bollinger(
    values: Sequence[float], n: int = 20, k: float = 2.0
) -> tuple[float, float, float] | None:
    """(mid, upper, lower) Bollinger bands at the last bar (population std)."""
    if n < 1 or len(values) < n:
        return None
    window = values[-n:]
    mid = sum(window) / n
    var = sum((v - mid) ** 2 for v in window) / n
    offset = k * var**0.5
    return mid, mid + offset, mid - offset
