"""Custom-code sandbox: subprocess execution of ``on_bar`` (design D6, task 8.4).

A code-track strategy never runs in-process. Each ``evaluate`` call spawns a
short-lived subprocess (``python -m fd_paper_trading.strategy.sandbox_worker``) that:

1. reads a JSON job on stdin: ``{source, bars, position, cash}``;
2. re-validates the source (AST allowlist) and compiles it;
3. sets rlimits (CPU 10 s, address space 256 MB - Linux-enforced, best-effort
   on macOS);
4. runs ``on_bar(ctx)`` in a restricted namespace (no imports, safe builtins
   only) and prints ``{"signals": [...]}`` (or ``{"error": ...}``) on stdout.

The host-side ``_SandboxBarEvaluator`` wraps this with a wall-clock timeout
(15 s) so a hung or runaway strategy can't block its run. A timeout or crash
marks that bar evaluation failed (backtest: run FAILED; live: bar skipped).

Per-evaluation subprocess spawn is the documented trade-off (D6): correct and
isolated, but slow for large code backtests. v1 code strategies are gated
behind ``allow_custom_code`` (default off), so this is rare in practice.
"""

from __future__ import annotations

import asyncio
import json
import resource
import sys
from decimal import Decimal

from ..marketdata.provider import Bar
from .backtester import BarEvaluator, EvaluationError
from .codegen import SAFE_BUILTINS, validate_source_strict
from .ctx import StrategyContext

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

    _set_limits()
    code = compile(source, "<strategy>", "exec")
    namespace: dict = {"__builtins__": SAFE_BUILTINS}
    try:
        exec(code, namespace)  # noqa: S102 - intentional restricted exec
    except Exception as exc:
        print(json.dumps({"error": f"source failed to load: {exc}"}))
        return
    on_bar = namespace.get("on_bar")
    if not callable(on_bar):
        print(json.dumps({"error": "source did not define on_bar(ctx)"}))
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


if __name__ == "__main__":
    main()
