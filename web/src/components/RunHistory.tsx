import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  useStrategyRuns,
  useStrategy,
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
 */
export default function RunHistory({ strategyId }: { strategyId: string }) {
  const qc = useQueryClient();
  const { data: runs, isLoading } = useStrategyRuns(strategyId);
  const { data: strategy } = useStrategy(strategyId);

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
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => {
              const st = r.stats ?? {};
              const ret = st.total_return;
              return (
                <tr key={r.id}>
                  <td>{modeLabel(r.mode)}</td>
                  <td className="muted">{windowOf(r)}</td>
                  <td>
                    <span className={runStatusClass(r.status)}>{r.status}</span>
                  </td>
                  <td className={ret !== undefined ? (Number(ret) >= 0 ? "pos" : "neg") : "muted"}>
                    {ret !== undefined ? pct(ret) : "-"}
                  </td>
                  <td>{st.sharpe !== undefined ? money(st.sharpe) : "-"}</td>
                  <td>{st.trade_count ?? "-"}</td>
                  <td className="muted">v{r.strategy_version}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {active && strategy && active.strategy_version < strategy.version && (
        <div className="warning-text">
          The active run is on v{active.strategy_version}; the strategy is now
          v{strategy.version}. Stop and restart to pick up the new version.
        </div>
      )}
    </div>
  );
}
