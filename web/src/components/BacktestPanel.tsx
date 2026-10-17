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
              {/* Extended stats come straight from the fd-quant compute plane via
                  the API - the panel renders them, never recomputes them. */}
              <Stat label="Annual return" value={pct(stats.annual_return)} />
              <Stat label="Annual volatility" value={pct(stats.annual_volatility)} />
              <Stat label="Sortino" value={money(stats.sortino)} />
              <Stat label="Calmar" value={money(stats.calmar)} />
              <Stat label="Turnover" value={pct(stats.turnover)} />
            </div>
          )}

          {run.cost_config?.enabled && run.cost_summary && (
            <div className="col" style={{ gap: 4 }}>
              <span className="muted">Costs</span>
              <div className="grid" style={{ gridTemplateColumns: "repeat(3, minmax(0, 1fr))" }}>
                <Stat label="Commission" value={money(Number(run.cost_summary.commission))} />
                <Stat label="Stamp tax" value={money(Number(run.cost_summary.stamp_tax))} />
                <Stat label="Slippage" value={money(Number(run.cost_summary.slippage))} />
              </div>
              <span className="muted" style={{ fontSize: 12 }}>
                commission {run.cost_config.commission_rate} · stamp tax{" "}
                {run.cost_config.stamp_tax_rate} · slippage {run.cost_config.slippage_bps}bps
              </span>
            </div>
          )}

          {(run.benchmark_results ?? []).length > 0 && (
            <div className="col" style={{ gap: 4 }}>
              <span className="muted">Benchmarks</span>
              <table className="table">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th>Return</th>
                    <th>Excess</th>
                    <th>Tracking error</th>
                    <th>Information ratio</th>
                  </tr>
                </thead>
                <tbody>
                  {(run.benchmark_results ?? []).map((b: any) => (
                    <tr key={b.symbol}>
                      <td>{b.symbol}</td>
                      {b.available ? (
                        <>
                          <td>{pct(b.stats?.total_return)}</td>
                          <td>{pct(b.relative?.excess_return)}</td>
                          <td>{pct(b.relative?.tracking_error)}</td>
                          <td>{money(b.relative?.information_ratio)}</td>
                        </>
                      ) : (
                        <td colSpan={4} className="muted">
                          unavailable — {b.reason}
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(run.walkforward_windows ?? []).length > 0 && (
            <div className="col" style={{ gap: 4 }}>
              <span className="muted">Walk-forward windows</span>
              <table className="table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Train</th>
                    <th>Test</th>
                    <th>Return</th>
                    <th>Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {(run.walkforward_windows ?? []).map((w: any) => (
                    <tr key={w.window}>
                      <td>{w.window}</td>
                      <td>
                        {w.train_start} → {w.train_end}
                      </td>
                      <td>
                        {w.test_start} → {w.test_end}
                      </td>
                      <td>{pct(w.stats?.total_return)}</td>
                      <td>{String(w.stats?.trade_count ?? 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {(run.scan_results ?? []).length > 0 && (
            <div className="col" style={{ gap: 4 }}>
              <span className="muted">Parameter scan</span>
              {run.in_sample && (
                <span className="muted" style={{ fontSize: 12 }}>
                  In-sample result — these parameters were scored on the same
                  window used to choose them; this is not a recommendation.
                </span>
              )}
              <table className="table">
                <thead>
                  <tr>
                    <th>Params</th>
                    <th>Return</th>
                    <th>Sharpe</th>
                    <th>Drawdown</th>
                    <th>Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {(run.scan_results ?? []).map((row: any, i: number) => (
                    <tr key={i}>
                      <td>{JSON.stringify(row.params)}</td>
                      <td>{pct(row.stats?.total_return)}</td>
                      <td>{money(row.stats?.sharpe)}</td>
                      <td>{pct(row.stats?.max_drawdown)}</td>
                      <td>{String(row.stats?.trade_count ?? 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
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
