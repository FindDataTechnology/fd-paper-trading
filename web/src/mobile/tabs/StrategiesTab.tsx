import { useState } from "react";
import {
  useStartPaperRun,
  useStopPaperRun,
  useStrategies,
  useStrategyRuns,
} from "../../api/hooks";
import { ACTIVE_RUN_STATUSES, type StrategyOut } from "../../api/types";
import { apiErrorMessage } from "../../api/client";
import { runStatusClass } from "../../lib/format";

/** Map strategy_id -> active run {id, status}, from the user's run list. */
function useActiveRunByStrategy(): Record<string, { id: string; status: string }> {
  const { data: runs } = useStrategyRuns();
  const out: Record<string, { id: string; status: string }> = {};
  if (!runs) return out;
  for (const r of runs) {
    if (ACTIVE_RUN_STATUSES.includes(r.status)) {
      // Most-recent active run wins (runs are newest-first).
      if (!out[r.strategy_id]) out[r.strategy_id] = { id: r.id, status: r.status };
    }
  }
  return out;
}

function StrategyCard({
  strategy,
  accountId,
  activeRun,
}: {
  strategy: StrategyOut;
  accountId: string | null;
  activeRun?: { id: string; status: string };
}) {
  const startRun = useStartPaperRun(strategy.id);
  const stopRun = useStopPaperRun();
  const [err, setErr] = useState<string | null>(null);

  function start() {
    if (!accountId) {
      setErr("Select or create an account first.");
      return;
    }
    setErr(null);
    startRun.mutate(
      { account_id: accountId },
      { onError: (e) => setErr(apiErrorMessage(e)) }
    );
  }

  function stop() {
    if (!activeRun) return;
    if (
      !window.confirm(
        "Stop this paper run? Resting limit orders will be cancelled; positions are not closed."
      )
    )
      return;
    setErr(null);
    stopRun.mutate(activeRun.id, {
      onError: (e) => setErr(apiErrorMessage(e)),
    });
  }

  return (
    <div className="m-card">
      <div className="m-between">
        <strong>{strategy.name}</strong>
        {activeRun ? (
          <span className={runStatusClass(activeRun.status)}>{activeRun.status}</span>
        ) : (
          <span className="muted" style={{ fontSize: 11 }}>
            idle
          </span>
        )}
      </div>
      <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
        {strategy.spec_summary ?? "-"} · v{strategy.version}
      </div>
      <div className="m-row" style={{ marginTop: 8 }}>
        {activeRun ? (
          <button
            className="secondary danger m-btn-sm"
            onClick={stop}
            disabled={stopRun.isPending}
          >
            {stopRun.isPending ? "Stopping…" : "Stop run"}
          </button>
        ) : (
          <button
            className="m-btn-sm"
            onClick={start}
            disabled={startRun.isPending || !accountId}
          >
            {startRun.isPending ? "Starting…" : "Start paper"}
          </button>
        )}
      </div>
      {err && (
        <div className="error-text" style={{ marginTop: 6 }}>
          {err}
        </div>
      )}
    </div>
  );
}

export default function StrategiesTab({
  accountId,
}: {
  accountId: string | null;
}) {
  const { data: strategies, isLoading, error } = useStrategies();
  const activeByStrategy = useActiveRunByStrategy();

  if (isLoading) return <div className="m-empty">Loading…</div>;
  if (error)
    return <div className="m-empty error-text">{apiErrorMessage(error)}</div>;
  if (!strategies || strategies.length === 0)
    return <div className="m-empty">No strategies yet.</div>;

  return (
    <div className="m-list">
      <div className="m-section-title">Strategies</div>
      {strategies.map((s) => (
        <StrategyCard
          key={s.id}
          strategy={s}
          accountId={accountId}
          activeRun={activeByStrategy[s.id]}
        />
      ))}
    </div>
  );
}
