import { useEffect, useRef, useState } from "react";
import {
  useStartBacktest,
  useStrategyRun,
  useStrategy,
} from "../api/hooks";
import { apiErrorMessage } from "../api/client";
import { money, pct, runStatusClass } from "../lib/format";
import EquityCurveChart from "./EquityCurveChart";

function todayMinus(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

/**
 * Backtest launcher + result viewer for a strategy.
 *
 * Submit POSTs a backtest (202 + run id); the run is then polled via
 * useStrategyRun (which auto-polls while active) and its stats + equity curve
 * are rendered on COMPLETED. The note calls out the fill-at-close semantics so
 * users don't expect intrabar execution.
 */
export default function BacktestPanel({ strategyId }: { strategyId: string }) {
  const startBacktest = useStartBacktest(strategyId);
  const [start, setStart] = useState(todayMinus(60));
  const [end, setEnd] = useState(todayMinus(0));
  const [initialCash, setInitialCash] = useState("100000");
  const [runId, setRunId] = useState<string | null>(null);
  const { data: run } = useStrategyRun(runId);
  const { data: strategy } = useStrategy(strategyId);

  // Reset the viewed run only when the strategy's version actually changes
  // (i.e. after a save bumps it) - not on the initial load, which would race
  // with a backtest launched before the strategy query resolves.
  const prevVersion = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (
      prevVersion.current !== undefined &&
      prevVersion.current !== strategy?.version
    ) {
      setRunId(null);
    }
    prevVersion.current = strategy?.version;
  }, [strategy?.version]);

  function submit() {
    startBacktest.mutate(
      { start, end, initial_cash: initialCash },
      { onSuccess: (r) => setRunId(r.id) },
    );
  }

  const status = run?.status;
  const stats = run?.stats ?? null;
  const points = run?.equity_points ?? [];

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <strong>Backtest</strong>

      <div className="row" style={{ alignItems: "flex-end" }}>
        <label className="col" style={{ gap: 4 }}>
          <span className="muted">Start</span>
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            aria-label="backtest start"
          />
        </label>
        <label className="col" style={{ gap: 4 }}>
          <span className="muted">End</span>
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            aria-label="backtest end"
          />
        </label>
        <label className="col" style={{ gap: 4 }}>
          <span className="muted">Initial cash</span>
          <input
            type="number"
            value={initialCash}
            onChange={(e) => setInitialCash(e.target.value)}
            aria-label="initial cash"
            style={{ width: 120 }}
          />
        </label>
        <button
          onClick={submit}
          disabled={startBacktest.isPending || !start || !end}
        >
          {startBacktest.isPending ? "Starting…" : "Run backtest"}
        </button>
      </div>

      {startBacktest.error && (
        <div className="error-text">{apiErrorMessage(startBacktest.error)}</div>
      )}

      <div className="muted" style={{ fontSize: 12 }}>
        Fills are computed at each bar's close (LIMIT orders fill if the bar's
        range crosses the limit). Backtested equity diverges from live paper
        runs, which fill on live quotes.
      </div>

      {run && (
        <div className="col" style={{ gap: 8 }}>
          <div className="row">
            <span className={runStatusClass(status ?? "")}>{status}</span>
            {run.error && <span className="error-text">{run.error}</span>}
          </div>

          {stats && (
            <div className="grid" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
              <Stat label="Total return" value={pct(stats.total_return)} cls={Number(stats.total_return) >= 0 ? "pos" : "neg"} />
              <Stat label="Max drawdown" value={pct(stats.max_drawdown)} cls="neg" />
              <Stat label="Sharpe" value={money(stats.sharpe)} />
              <Stat label="Win rate" value={pct(stats.win_rate)} />
              <Stat label="Trades" value={String(stats.trade_count ?? 0)} />
              <Stat label="Final equity" value={money(stats.final_equity)} />
            </div>
          )}

          {points.length > 0 && status === "COMPLETED" && (
            <div className="col">
              <span className="muted">Equity curve</span>
              <EquityCurveChart points={points} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  cls,
}: {
  label: string;
  value: string;
  cls?: string;
}) {
  return (
    <div className="card" style={{ padding: 8 }}>
      <div className="muted" style={{ fontSize: 12 }}>
        {label}
      </div>
      <div className={cls}>{value}</div>
    </div>
  );
}
