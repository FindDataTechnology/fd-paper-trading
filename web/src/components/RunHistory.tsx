import { useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useStrategyRuns,
  useStrategy,
  useCompareRuns,
} from "../api/hooks";
import { ACTIVE_RUN_STATUSES } from "../api/types";
import type { StrategyEventPayload } from "../realtime/useRealtime";
import { useStrategyEvents } from "../realtime/useRealtime";
import { money, pct, runStatusClass } from "../lib/format";

function modeLabel(mode: string): string {
  return mode === "PAPER" ? "Live paper" : "Backtest";
}

function windowOf(r: {
  mode: string;
  params: Record<string, unknown> | null;
}): string {
  if (r.mode === "BACKTEST") {
    const p = r.params ?? {};
    return `${p.start ?? "-"} → ${p.end ?? "-"}`;
  }
  return "ongoing";
}

/**
 * History of runs for a strategy, plus a live status row that refreshes on
 * inbound /ws strategy events (state changes, orders, rejections, errors).
 *
 * A stopped/complete run now carries stats (paper runs included, since change
 * add-paper-run-metrics), so the Return/Sharpe/Trades columns are real for both
 * modes. Any two runs can be compared; the comparison **refuses** when the runs
 * were measured under different rules and shows why, rather than a misleading
 * diff (design D6/D7).
 */
export default function RunHistory({ strategyId }: { strategyId: string }) {
  const qc = useQueryClient();
  const { data: runs, isLoading } = useStrategyRuns(strategyId);
  const { data: strategy } = useStrategy(strategyId);
  const [pair, setPair] = useState<{ a: string; b: string } | null>(null);

  const onEvent = useCallback(
    (e: StrategyEventPayload) => {
      if (e.strategy_id !== strategyId) return;
      qc.invalidateQueries({ queryKey: ["strategy-runs"] });
      qc.invalidateQueries({ queryKey: ["strategy-run"] });
      qc.invalidateQueries({ queryKey: ["strategy", strategyId] });
    },
    [qc, strategyId],
  );
  useStrategyEvents(onEvent);

  const active = runs?.find((r) => ACTIVE_RUN_STATUSES.includes(r.status));

  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Run history</strong>
        {active && (
          <span className={runStatusClass(active.status)}>
            ● {modeLabel(active.mode)} run {active.status}
          </span>
        )}
      </div>

      {isLoading && <div className="muted">Loading…</div>}

      {runs && runs.length === 0 && (
        <div className="muted">No runs yet. Start a backtest or paper run.</div>
      )}

      {runs && runs.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Mode</th>
              <th>Window</th>
              <th>Status</th>
              <th>Return</th>
              <th>Sharpe</th>
              <th>Trades</th>
              <th>Version</th>
              {runs.length >= 2 && <th />}
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => {
              const st = r.stats ?? {};
              const ret = num(st.total_return);
              const sharpe = num(st.sharpe);
              const trades = num(st.trade_count);
              const other = runs.find((x) => x.id !== r.id);
              return (
                <tr key={r.id}>
                  <td>{modeLabel(r.mode)}</td>
                  <td className="muted">{windowOf(r)}</td>
                  <td>
                    <span className={runStatusClass(r.status)}>{r.status}</span>
                  </td>
                  <td className={ret != null ? (ret >= 0 ? "pos" : "neg") : "muted"}>
                    {ret != null ? pct(ret) : "-"}
                  </td>
                  <td>{sharpe != null ? money(sharpe) : "-"}</td>
                  <td>{trades != null ? trades : "-"}</td>
                  <td className="muted">v{r.strategy_version}</td>
                  {other && (
                    <td>
                      <button
                        className="secondary"
                        onClick={() => setPair({ a: r.id, b: other.id })}
                      >
                        Compare
                      </button>
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {pair && <ComparisonPanel a={pair.a} b={pair.b} onClose={() => setPair(null)} />}

      {active && strategy && active.strategy_version < strategy.version && (
        <div className="warning-text">
          The active run is on v{active.strategy_version}; the strategy is now
          v{strategy.version}. Stop and restart to pick up the new version.
        </div>
      )}
    </div>
  );
}

const COMPARED: { key: string; label: string; money?: boolean }[] = [
  { key: "total_return", label: "Total return" },
  { key: "max_drawdown", label: "Max drawdown" },
  { key: "sharpe", label: "Sharpe" },
  { key: "trade_count", label: "Trades" },
];

function ComparisonPanel({
  a,
  b,
  onClose,
}: {
  a: string;
  b: string;
  onClose: () => void;
}) {
  const { data, isLoading, error } = useCompareRuns(a, b);
  return (
    <div className="card" style={{ background: "var(--bg-2)" }}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <strong>Run comparison</strong>
        <button className="secondary" onClick={onClose}>
          Close
        </button>
      </div>
      {isLoading && <div className="muted">Comparing…</div>}
      {error && <div className="error-text">Could not compare these runs.</div>}
      {data && !data.comparable && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          <div className="warning-text">
            These runs are not comparable. No difference is shown, because a
            difference between differently-measured numbers is noise.
          </div>
          <ul className="muted" style={{ margin: 0, paddingLeft: 18 }}>
            {data.reasons.map((r) => (
              <li key={r.dimension}>
                <strong>{r.dimension}</strong>: {r.detail}
              </li>
            ))}
          </ul>
        </div>
      )}
      {data && data.comparable && data.stats && (
        <table>
          <thead>
            <tr>
              <th>Stat</th>
              <th>This run</th>
              <th>Other</th>
              <th>Diff</th>
            </tr>
          </thead>
          <tbody>
            {COMPARED.map(({ key, label }) => {
              const self = data.stats!.self[key];
              const oth = data.stats!.other[key];
              const diff = data.stats!.diff[key];
              return (
                <tr key={key}>
                  <td>{label}</td>
                  <td>{fmt(self)}</td>
                  <td>{fmt(oth)}</td>
                  <td className={diff != null ? (diff >= 0 ? "pos" : "neg") : "muted"}>
                    {diff != null ? fmt(diff, true) : "-"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

function fmt(v: number | null, signed = false): string {
  if (v == null) return "-";
  const s = Math.abs(v) < 1 ? pct(v) : money(v);
  return signed && v >= 0 ? `+${s}` : s;
}

/** Narrow a stats value (which may be a number, a notes array, or null). */
function num(v: number | string[] | null | undefined): number | null {
  return typeof v === "number" ? v : null;
}