"""Custom-code sandbox: subprocess execution of ``on_bar`` / ``target_weights``.

A code-track strategy never runs in-process. Each ``evaluate`` call spawns a
short-lived subprocess (``python -m fd_paper_trading.strategy.sandbox_worker``) that:

1. reads a JSON job on stdin: ``{source, bars, position, cash}`` for the
   single-symbol track (that job has no ``entry_point`` key, and keeps working
   unchanged) or ``{source, entry_point: "target_weights", universe, date,
   series, cash, positions}`` for the weight track;
2. re-validates the source (AST allowlist) against the job's entry point and
   compiles it;
3. sets rlimits (CPU 10 s, address space 256 MB - Linux-enforced, best-effort
   on macOS);
4. runs the entry point in a restricted namespace (no imports, safe builtins
   only) and prints ``{"signals": [...]}`` for ``on_bar`` or
   ``{"target_weights": {...}}`` for ``target_weights`` (or ``{"error": ...}``)
   on stdout.

The host-side ``_SandboxBarEvaluator`` wraps this with a wall-clock timeout
(15 s) so a hung or runaway strategy can't block its run. A timeout or crash
marks that bar evaluation failed (backtest: run FAILED; live: bar skipped).

Per-evaluation subprocess spawn is the documented trade-off (D6): correct and
isolated, but slow for large code backtests. v1 code strategies are gated
behind ``allow_custom_code`` (default off), so this is rare in practice.

``_WeightSandboxEvaluator`` is the weight track's counterpart: same subprocess,
same timeout and error handling, but synchronous — ``replay_weights`` is a
synchronous engine that calls ``evaluate_code(ctx)`` once per rebalance point,
so it cannot await. A caller on an event loop drives the replay in a worker
thread rather than blocking the loop.
"""

from __future__ import annotations

import asyncio
import json
import resource
import subprocess
import sys
from datetime import date
from decimal import Decimal
from typing import Mapping

from ..marketdata.provider import Bar
from .backtester import BarEvaluator, EvaluationError
from .codegen import SAFE_BUILTINS, validate_source_strict
from .ctx import StrategyContext, SymbolSeries, WeightContext

_WALL_CLOCK_TIMEOUT = 15.0  # seconds; > CPU rlimit so rlimit fires first on hangs
_AS_LIMIT = 256 * 1024 * 1024  # 256 MB


def _set_limits() -> None:
    """CPU 10 s + address space 256 MB (Linux-enforced; best-effort on macOS)."""
    for limit, value in (
        (resource.RLIMIT_CPU, (10, 10)),
        (resource.RLIMIT_AS, (_AS_LIMIT, _AS_LIMIT)),
    ):
        try:
            resource.setrlimit(limit, value)
        except (ValueError, OSError):  # pragma: no cover - platform-specific
            pass


def _bar_to_dict(bar: Bar) -> dict:
    return {
        "ts": bar.ts,
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": str(bar.volume),
    }


def _bar_from_dict(d: dict) -> Bar:
    return Bar(
        ts=d["ts"],
        open=Decimal(d["open"]),
        high=Decimal(d["high"]),
        low=Decimal(d["low"]),
        close=Decimal(d["close"]),
        volume=Decimal(d["volume"]),
    )


def main() -> None:
    """CLI entry: read one JSON job on stdin, print one JSON result on stdout."""
    raw = sys.stdin.read()
    try:
        job = json.loads(raw)
        source = job["source"]
    except Exception as exc:
        print(json.dumps({"error": f"bad job: {exc}"}))
        return

    # An absent entry_point means the original single-symbol job shape, so an
    # existing caller's job keeps taking exactly the path it always did.
    if job.get("entry_point") == "target_weights":
        _run_target_weights(job, source)
    else:
        _run_on_bar(job, source)


def _run_on_bar(job: dict, source: str) -> None:
    try:
        bars = [_bar_from_dict(b) for b in job["bars"]]
        position = Decimal(job["position"])
        cash = Decimal(job["cash"])
    except Exception as exc:
        print(json.dumps({"error": f"bad job: {exc}"}))
        return

    errors = None
    try:
        validate_source_strict(source)
    except Exception as exc:  # CodeValidationError or similar
        errors = str(exc)
    if errors:
        print(json.dumps({"error": f"source invalid: {errors}"}))
        return

    on_bar = _load_entry_point(source, "on_bar")
    if isinstance(on_bar, str):
        print(json.dumps({"error": on_bar}))
        return

    ctx = StrategyContext(bars, position=position, cash=cash)
    try:
        on_bar(ctx)
    except Exception as exc:
        print(json.dumps({"error": f"on_bar raised: {exc}"}))
        return

    signals = [
        {
            "side": s.side,
            "quantity": str(s.quantity),
            "limit_price": str(s.limit_price) if s.limit_price is not None else None,
        }
        for s in ctx.signals
    ]
    print(json.dumps({"signals": signals}))


def _run_target_weights(job: dict, source: str) -> None:
    """Weight-track job: rebuild the context from the job, call ``target_weights``."""
    try:
        ctx = _weight_context(job)
    except Exception as exc:
        print(json.dumps({"error": f"bad job: {exc}"}))
        return

    errors = None
    try:
        validate_source_strict(source, entry_point="target_weights")
    except Exception as exc:  # CodeValidationError or similar
        errors = str(exc)
    if errors:
        print(json.dumps({"error": f"source invalid: {errors}"}))
        return

    target_weights = _load_entry_point(source, "target_weights")
    if isinstance(target_weights, str):
        print(json.dumps({"error": target_weights}))
        return

    try:
        returned = target_weights(ctx)
    except Exception as exc:
        print(json.dumps({"error": f"target_weights raised: {exc}"}))
        return
    try:
        weights = {str(k): float(v) for k, v in dict(returned).items()}
    except Exception as exc:
        print(json.dumps({
            "error": f"target_weights must return a {{symbol: weight}} mapping: {exc}"
        }))
        return
    print(json.dumps({"target_weights": weights}))


def _load_entry_point(source: str, name: str):
    """Compile and load the named top-level function; a string result is an error."""
    _set_limits()
    try:
        code = compile(source, "<strategy>", "exec")
    except Exception as exc:
        return f"source failed to load: {exc}"
    namespace: dict = {"__builtins__": SAFE_BUILTINS}
    try:
        exec(code, namespace)  # noqa: S102 - intentional restricted exec
    except Exception as exc:
        return f"source failed to load: {exc}"
    func = namespace.get(name)
    if not callable(func):
        return f"source did not define {name}(ctx)"
    return func


def _weight_context(job: dict) -> WeightContext:
    """Rebuild a ``WeightContext`` inside the subprocess from the job's payload.

    Everything is reconstructed from plain JSON values: the universe names the
    allowed symbols, each carries its own bar list, and the portfolio is the
    cash plus per-symbol integer positions the host had at that rebalance.
    """
    universe = [str(sym) for sym in job["universe"]]
    if not universe:
        raise ValueError("a weight job requires a non-empty universe")
    series = {
        sym: SymbolSeries(
            sym, [_bar_from_dict(b) for b in (job["series"].get(sym) or [])]
        )
        for sym in universe
    }
    return WeightContext(
        day=date.fromisoformat(job["date"]),
        universe=universe,
        series=series,
        cash=Decimal(job["cash"]),
        positions={k: Decimal(v) for k, v in (job.get("positions") or {}).items()},
    )


class _SandboxBarEvaluator:
    """Evaluates a code-track strategy bar-by-bar in isolated subprocesses."""

    def __init__(self, source: str) -> None:
        self._source = source

    async def evaluate(self, ctx: StrategyContext) -> None:
        job = {
            "source": self._source,
            "bars": [_bar_to_dict(b) for b in ctx._bars],
            "position": str(ctx.position),
            "cash": str(ctx.cash),
        }
        result = await self._run_subprocess(job)
        if result is None:
            raise EvaluationError("sandbox produced no output (timeout or crash)")
        if "error" in result:
            raise EvaluationError(f"sandbox error: {result['error']}")
        for s in result.get("signals", []):
            qty = Decimal(s["quantity"])
            lp = Decimal(s["limit_price"]) if s.get("limit_price") is not None else None
            if s["side"] == "BUY":
                ctx.buy(qty, lp)
            else:
                ctx.sell(qty, lp)

    async def _run_subprocess(self, job: dict) -> dict | None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "fd_paper_trading.strategy.sandbox_worker",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, _err = await asyncio.wait_for(
                proc.communicate(json.dumps(job).encode()),
                timeout=_WALL_CLOCK_TIMEOUT,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return None
        try:
            return json.loads(out.decode())
        except (ValueError, UnicodeDecodeError):
            return None


class _WeightSandboxEvaluator:
    """Evaluates ``target_weights(ctx)`` once per rebalance in a subprocess.

    The replay engine is synchronous (``replay_weights`` calls
    ``evaluate_code(ctx)`` and consumes the returned mapping), so this evaluator
    is synchronous too; a caller on an event loop runs the whole replay in a
    worker thread (``asyncio.to_thread``) rather than blocking the loop. One
    subprocess per rebalance point, with the same wall-clock timeout and the
    same failure semantics as the per-bar evaluator.
    """

    def __init__(self, source: str) -> None:
        self._source = source

    def evaluate(self, ctx: WeightContext) -> Mapping[str, float]:
        """Run the source's ``target_weights(ctx)``; return its mapping."""
        universe = ctx.universe
        job = {
            "source": self._source,
            "entry_point": "target_weights",
            "universe": universe,
            "date": ctx.date.isoformat(),
            # The engine hands over the series already truncated to the decision
            # date, so what crosses the process boundary is exactly what a
            # portfolio manager could have seen then - nothing newer.
            "series": {sym: [_bar_to_dict(b) for b in ctx.series(sym).bars] for sym in universe},
            "cash": str(ctx.cash),
            "positions": {
                sym: str(ctx.position(sym))
                for sym in universe
                if ctx.position(sym) != 0
            },
        }
        result = self._run_subprocess(job)
        if result is None:
            raise EvaluationError("sandbox produced no output (timeout or crash)")
        if "error" in result:
            raise EvaluationError(f"sandbox error: {result['error']}")
        return result.get("target_weights", {})

    def _run_subprocess(self, job: dict) -> dict | None:
        """One subprocess, one JSON job in / one JSON result out, bounded in time."""
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "fd_paper_trading.strategy.sandbox_worker"],
                input=json.dumps(job).encode(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=_WALL_CLOCK_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return None
        try:
            return json.loads(proc.stdout.decode())
        except (ValueError, UnicodeDecodeError):
            return None


if __name__ == "__main__":
    main()
