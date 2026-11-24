"""Comparability of two strategy runs (change add-paper-run-metrics).

## What "comparable" means here

Two runs are the *same experiment* only when everything that could move the
numbers is the same: strategy + version, overlapping window, initial cash, cost
model, simulation profile (settlement, lot, price world). The moment one differs,
the difference between the two statistics is measuring the difference between two
assumptions — not the strategy.

So this module's job is to *refuse* rather than compare. The refusal carries the
dimension that differed, because "these aren't comparable" without saying why is
the same as not checking.

## Why the shapes are what they are

- ``stats`` is ``None`` exactly when ``comparable`` is ``False``. A zero or an
  empty diff would read as "measured, and identical", which is the opposite of
  what an incomparable pair means.
- ``reasons`` is a list of ``{dimension, detail}`` — machine-readabl because the
  UI and any later gate both need to point at the offending dimension.

The precedent is ``scripts/compare_backtest_fingerprints.py``: incomparability is
data, and ``stats_identical: None`` (not compared) is deliberately distinct from
``True`` (compared and equal).
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Optional

#: Minimum overlapping trading days for a comparison to mean anything; matches
#: the shared stats module's two-point floor.
MIN_OVERLAP_DAYS = 2

#: Stats fields compared field-by-field when the pair is comparable.
COMPARED_STATS = (
    "total_return",
    "max_drawdown",
    "sharpe",
    "win_rate",
    "trade_count",
    "final_equity",
    "annual_return",
    "annual_volatility",
    "sortino",
    "calmar",
)


def run_window(run: Any, equity_dates: list[date] | None = None) -> Optional[tuple[date, date]]:
    """The run's effective window.

    A backtest states it explicitly in ``params``; a paper run's window is the
    span of its equity points (there is no requested end). Returns ``None`` when
    neither is available — that run has no measurable window.
    """
    params = getattr(run, "params", None) or {}
    if getattr(run, "mode", None) == "BACKTEST":
        start = _as_date(params.get("start"))
        end = _as_date(params.get("end"))
        if start and end:
            return (start, end)
    if equity_dates:
        return (min(equity_dates), max(equity_dates))
    return None


def compare_runs(
    *,
    run: Any,
    other: Any,
    run_window_: Optional[tuple[date, date]],
    other_window: Optional[tuple[date, date]],
    run_stats: Mapping[str, Any] | None,
    other_stats: Mapping[str, Any] | None,
) -> dict:
    """Compare two runs and return the machine-readable result.

    ``run`` is the "self" side; diffs are ``other - self``.
    """
    reasons: list[dict] = []

    if getattr(run, "strategy_id", None) != getattr(other, "strategy_id", None):
        reasons.append({
            "dimension": "strategy",
            "detail": "runs belong to different strategies",
        })
    elif getattr(run, "strategy_version", None) != getattr(other, "strategy_version", None):
        reasons.append({
            "dimension": "strategy_version",
            "detail": (
                f"versions differ ({run.strategy_version} vs {other.strategy_version}); "
                "different code produced these numbers"
            ),
        })

    overlap = _overlap(run_window_, other_window)
    if overlap is None or (overlap[1] - overlap[0]).days < MIN_OVERLAP_DAYS - 1:
        reasons.append({
            "dimension": "window",
            "detail": (
                "no usable overlapping window"
                if overlap is None
                else f"overlap {overlap[0]}..{overlap[1]} needs >= {MIN_OVERLAP_DAYS} "
                "trading days"
            ),
        })

    _compare_param(reasons, "initial_cash", run, other)
    _compare_profile(reasons, run, other)

    # A run with no headline stats did not produce a measurement — a FAILED
    # backtest, or one still running. Comparing it yields an all-null diff table,
    # which reads as "measured, identical to nothing". That must be a refusal.
    # (Found by real e2e: a COMPLETED run vs a FAILED run came back comparable
    # with every diff null — exactly the "#reasons non-empty and comparable:true
    # must not coexist" line this module is supposed to hold.)
    self_missing = not _has_stats(run_stats)
    other_missing = not _has_stats(other_stats)
    if self_missing or other_missing:
        who = []
        if self_missing:
            who.append(f"this run ({getattr(run, 'status', '?')})")
        if other_missing:
            who.append(f"the other run ({getattr(other, 'status', '?')})")
        reasons.append({
            "dimension": "stats",
            "detail": (
                "no headline statistics for " + " and ".join(who)
                + "; a run without stats has no numbers to compare"
            ),
        })

    comparable = not reasons
    return {
        "comparable": comparable,
        "reasons": reasons,
        "window": {
            "self": _window_dict(run_window_),
            "other": _window_dict(other_window),
            "overlap": _window_dict(overlap),
        },
        "runs": {
            "self": _run_identity(run),
            "other": _run_identity(other),
        },
        "stats": _stats_block(run_stats, other_stats) if comparable else None,
    }


def _stats_block(self_stats, other_stats) -> dict:
    self_s = self_stats or {}
    other_s = other_stats or {}
    diffs: dict[str, float | None] = {}
    for key in COMPARED_STATS:
        a = self_s.get(key)
        b = other_s.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            diffs[key] = round(float(b) - float(a), 6)
        else:
            diffs[key] = None
    return {
        "self": {k: self_s.get(k) for k in COMPARED_STATS},
        "other": {k: other_s.get(k) for k in COMPARED_STATS},
        "diff": diffs,
    }


def _compare_param(reasons, key, run, other) -> None:
    a = (getattr(run, "params", None) or {}).get(key)
    b = (getattr(other, "params", None) or {}).get(key)
    if a != b:
        reasons.append({
            "dimension": key,
            "detail": f"{key} differs ({a} vs {b})",
        })


def _compare_profile(reasons, run, other) -> None:
    a = _profile(run)
    b = _profile(other)
    if a is None or b is None:
        reasons.append({
            "dimension": "simulation_profile",
            "detail": (
                "one run predates the simulation profile (its rules are unmodelled) "
                "so its numbers cannot be placed beside another run's"
            ),
        })
        return
    for dim in ("cost", "settlement", "lot", "price_world"):
        if a.get(dim) != b.get(dim):
            reasons.append({
                "dimension": f"simulation_profile.{dim}",
                "detail": f"{dim} differs ({a.get(dim)} vs {b.get(dim)})",
            })


def _profile(run) -> Optional[dict]:
    params = getattr(run, "params", None) or {}
    profile = params.get("simulation_profile")
    return dict(profile) if isinstance(profile, Mapping) else None


def _has_stats(stats: Mapping[str, Any] | None) -> bool:
    """A run has usable stats when it recorded a non-empty stats mapping.

    A FAILED or still-running run has ``stats`` null; a completed one has the
    headline fields. Used to refuse comparisons where one side is not a
    measurement at all, rather than emitting an all-null diff table.
    """
    return isinstance(stats, Mapping) and len(stats) > 0


def _run_identity(run) -> dict:
    return {
        "id": str(getattr(run, "id", "")),
        "mode": getattr(run, "mode", None),
        "strategy_version": getattr(run, "strategy_version", None),
        "simulation_profile": _profile(run),
    }


def _overlap(
    a: Optional[tuple[date, date]], b: Optional[tuple[date, date]]
) -> Optional[tuple[date, date]]:
    if a is None or b is None:
        return None
    start = max(a[0], b[0])
    end = min(a[1], b[1])
    return (start, end) if start <= end else None


def _window_dict(w: Optional[tuple[date, date]]) -> dict | None:
    if w is None:
        return None
    return {"start": w[0].isoformat(), "end": w[1].isoformat()}


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None
