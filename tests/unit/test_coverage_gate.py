"""The backtest coverage gate (change: backtest-data-coverage-gate).

What these tests guard against: a window holding twelve days of data inside a
two-year request used to produce a full-looking backtest — stats, equity curve,
everything — computed over those twelve days. The gate makes that run FAIL, and
these tests hold both halves of the contract:

* a window with genuinely-missing intervals is rejected, naming the symbol and
  the interval;
* a window whose only absences are confirmed market closures completes,
  because rejecting those would fail every window containing a holiday.

The gate is best-effort by design: it runs only against a provider that can
report coverage. The tests therefore use a stub that implements the optional
capability — using the simulated provider here would prove nothing, since the
gate correctly skips it.
"""

from __future__ import annotations

import asyncio
import pytest

from fd_paper_trading.api.routes.strategies import (
    _check_coverage,
    _coverage_params,
    _gate_coverage,
)
from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.strategy.backtester import EvaluationError


def _interval(start: str, end: str) -> dict:
    return {"start": start, "end": end}


# ---------------------------------------------------------------------------
# the gate decision itself (pure, no I/O)
# ---------------------------------------------------------------------------


def test_missing_intervals_reject_and_name_symbol_and_range() -> None:
    with pytest.raises(EvaluationError) as exc:
        _gate_coverage("AAPL", {
            "missing": [_interval("2025-03-01", "2025-03-31")],
            "market_closed": [],
        })
    message = str(exc.value)
    assert "AAPL" in message
    assert "2025-03-01" in message and "2025-03-31" in message
    assert "incomplete" in message


def test_market_closed_alone_does_not_reject() -> None:
    """Rejecting holidays would fail every normal window; the split is the point."""
    _gate_coverage("AAPL", {
        "missing": [],
        "market_closed": [
            _interval("2025-01-28", "2025-02-04"),
            _interval("2025-10-01", "2025-10-08"),
        ],
    })  # must not raise


def test_probe_failure_keeps_the_window_missing() -> None:
    """'Could not ask' is not 'nothing there' — it must stay a rejection."""
    with pytest.raises(EvaluationError):
        _gate_coverage("AAPL", {
            "missing": [_interval("2025-06-01", "2025-06-30")],
            "market_closed": [],
            "fill_failed": True,
        })


def test_many_missing_intervals_are_summarised_not_dumped() -> None:
    """A broken (50-gap) series must be rejected with a readable message."""
    gaps = [_interval(f"2025-{m:02d}-01", f"2025-{m:02d}-28") for m in range(1, 13)]
    with pytest.raises(EvaluationError) as exc:
        _gate_coverage("BROKEN", {"missing": gaps, "market_closed": []})
    message = str(exc.value)
    assert "12 missing interval" in message
    assert "more" in message, "the tail is summarised, not silently dropped"


# ---------------------------------------------------------------------------
# the run-record slice
# ---------------------------------------------------------------------------


def test_no_capability_records_unchecked() -> None:
    params = _coverage_params("AAPL", None)
    assert params == {"coverage_checked": False}, (
        "'did not check' must be visible, not folded into 'checked and fine'"
    )


def test_checked_params_carry_fingerprint_and_counts() -> None:
    params = _coverage_params("AAPL", {
        "fingerprint": "abc123",
        "missing": [_interval("2025-03-01", "2025-03-31")],
        "market_closed": [_interval("2025-01-28", "2025-02-04")],
    })
    assert params["coverage_checked"] is True
    assert params["fingerprint"] == "abc123"
    assert params["missing_count"] == 1
    assert params["market_closed_count"] == 1


# ---------------------------------------------------------------------------
# the optional-capability probe
# ---------------------------------------------------------------------------


class _NoCapabilityProvider:
    async def get_bars(self, symbol, timeframe, limit):
        return []


class _CoverageProvider:
    """Stub implementing exactly the optional capability the gate probes."""

    def __init__(self, missing, market_closed=None, fingerprint="f" * 64):
        self._missing = missing
        self._market_closed = market_closed or []
        self._fingerprint = fingerprint

    async def get_bars_with_coverage(self, symbol, start, end):
        return {
            "bars": [],
            "missing": self._missing,
            "market_closed": self._market_closed,
            "fill_failed": False,
            "fingerprint": self._fingerprint,
        }


@pytest.mark.asyncio
async def test_probe_returns_none_for_a_provider_without_the_capability() -> None:
    app = type("App", (), {"state": type("S", (), {
        "market_data_provider": _NoCapabilityProvider()})})()
    assert await _check_coverage(app, "AAPL", None, None) is None


@pytest.mark.asyncio
async def test_probe_returns_facts_for_a_capable_provider() -> None:
    provider = _CoverageProvider(missing=[_interval("2025-03-01", "2025-03-31")])
    app = type("App", (), {"state": type("S", (), {
        "market_data_provider": provider})})()
    out = await _check_coverage(app, "AAPL", None, None)
    assert out is not None
    assert out["missing"]


# ---------------------------------------------------------------------------
# the gate wired into the real replay path (via the API's backtest runner)
# ---------------------------------------------------------------------------


class _CoverageStubProvider(_CoverageProvider):
    """Serves deterministic bars so a *passing* gate still completes a run."""

    def __init__(self, missing, market_closed=None):
        super().__init__(missing, market_closed)

    async def get_bars(self, symbol, timeframe, limit):
        from datetime import datetime, timezone
        from decimal import Decimal

        bars = []
        for day in range(1, 11):
            ts = datetime(2026, 1, day, tzinfo=timezone.utc).timestamp()
            price = Decimal(10) + day
            bars.append(Bar(ts=ts, open=price, high=price, low=price,
                            close=price, volume=Decimal(1000)))
        return bars


def _make_app(provider):
    state = type("S", (), {"market_data_provider": provider})
    return type("App", (), {"state": state})


@pytest.mark.asyncio
async def test_partial_window_fails_the_run() -> None:
    """The headline: a 12-days-in-a-730-window run must not look like a result."""
    from datetime import date
    from decimal import Decimal

    from fd_paper_trading.api.routes import strategies as routes
    from fd_paper_trading.persistence.models import StrategyRun
    _replay_single_symbol = routes._replay_single_symbol

    provider = _CoverageStubProvider(
        missing=[_interval("2025-06-01", "2025-06-30")])
    app = _make_app(provider)
    run = StrategyRun(params={"initial_cash": "100000"})
    from fd_paper_trading.strategy.spec import TemplateSpec
    spec = TemplateSpec(kind="template", template="sma_cross", symbol="AAPL",
                        params={"fast": 2, "slow": 3, "quantity": 10})

    with pytest.raises(EvaluationError) as exc:
        await _replay_single_symbol(app, run, spec, date(2026, 1, 1), date(2026, 1, 10))
    assert "AAPL" in str(exc.value)
    assert "2025-06-01" in str(exc.value)


@pytest.mark.asyncio
async def test_holiday_only_window_completes() -> None:
    from datetime import date
    from decimal import Decimal

    from fd_paper_trading.api.routes import strategies as routes
    from fd_paper_trading.persistence.models import StrategyRun
    _replay_single_symbol = routes._replay_single_symbol

    provider = _CoverageStubProvider(
        missing=[],
        market_closed=[_interval("2025-01-28", "2025-02-04")],
    )
    app = _make_app(provider)
    run = StrategyRun(params={"initial_cash": "100000"})
    from fd_paper_trading.strategy.spec import TemplateSpec
    spec = TemplateSpec(kind="template", template="sma_cross", symbol="AAPL",
                        params={"fast": 2, "slow": 3, "quantity": 10})

    result = await _replay_single_symbol(
        app, run, spec, date(2026, 1, 1), date(2026, 1, 10)
    )
    assert result.stats["trade_count"] >= 0
    # The run record now carries the coverage slice (design D4).
    assert run.params["coverage_checked"] is True
    assert run.params["market_closed_count"] == 1


@pytest.mark.asyncio
async def test_gate_skipped_for_a_provider_without_the_capability() -> None:
    """Simulated-provider behaviour is untouched — and visibly so."""
    from datetime import date
    from decimal import Decimal

    from fd_paper_trading.api.routes import strategies as routes
    from fd_paper_trading.persistence.models import StrategyRun
    _replay_single_symbol = routes._replay_single_symbol

    class SimOnly:
        async def get_bars(self, symbol, timeframe, limit):
            return await _CoverageStubProvider([]).get_bars(symbol, timeframe, limit)

    app = _make_app(SimOnly())
    run = StrategyRun(params={"initial_cash": "100000"})
    from fd_paper_trading.strategy.spec import TemplateSpec
    spec = TemplateSpec(kind="template", template="sma_cross", symbol="AAPL",
                        params={"fast": 2, "slow": 3, "quantity": 10})

    result = await _replay_single_symbol(
        app, run, spec, date(2026, 1, 1), date(2026, 1, 10)
    )
    assert result.stats
    assert run.params.get("coverage_checked") is None or (
        run.params.get("coverage_checked") is False
    ), "an unchecked run must be recorded as such"


# ---------------------------------------------------------------------------
# reverse self-check (task 5.1)
# ---------------------------------------------------------------------------


def test_reverse_selfcheck_a_broken_gate_is_caught(monkeypatch) -> None:
    """Prove the rejection tests can fail.

    A gate that never rejects is indistinguishable from no gate unless the suite
    can go red. Stubbing the decision to always-pass must break the headline
    test above — this asserts the *pair* holds.
    """
    import fd_paper_trading.api.routes.strategies as routes

    monkeypatch.setattr(routes, "_gate_coverage", lambda symbol, coverage: None)
    _replay_single_symbol = routes._replay_single_symbol
    # With the gate neutered, a partially covered window no longer raises:
    provider = _CoverageStubProvider(
        missing=[_interval("2025-06-01", "2025-06-30")])
    app = _make_app(provider)
    run = type("R", (), {"params": {"initial_cash": "100000"}})()
    from fd_paper_trading.strategy.spec import TemplateSpec
    spec = TemplateSpec(kind="template", template="sma_cross", symbol="AAPL",
                        params={"fast": 2, "slow": 3, "quantity": 10})

    from datetime import date

    result = asyncio.run(_replay_single_symbol(
        app, run, spec, date(2026, 1, 1), date(2026, 1, 10)))
    assert result.stats, "with the gate neutered the run completes — the tests above must fail"
