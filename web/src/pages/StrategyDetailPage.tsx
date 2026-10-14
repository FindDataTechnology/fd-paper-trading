import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  useStrategy,
  useStrategyTemplates,
  useUpdateStrategy,
  useDeleteStrategy,
  useStartPaperRun,
  useStopPaperRun,
  useStrategyRuns,
} from "../api/hooks";
import { ACTIVE_RUN_STATUSES } from "../api/types";
import type { StrategySpec } from "../api/types";
import { apiErrorMessage } from "../api/client";
import { useUI } from "../store/ui";
import { useNavigate } from "react-router-dom";
import StrategyEditor from "../components/StrategyEditor";
import BacktestPanel from "../components/BacktestPanel";
import RunHistory from "../components/RunHistory";

export default function StrategyDetailPage() {
  const { strategyId = "" } = useParams<{ strategyId: string }>();
  const { data: strategy, isLoading, error } = useStrategy(strategyId);
  const { data: templates } = useStrategyTemplates();
  const update = useUpdateStrategy(strategyId);
  const del = useDeleteStrategy();
  const navigate = useNavigate();

  const activeAccountId = useUI((s) => s.activeAccountId);
  const startPaper = useStartPaperRun(strategyId);
  const stopPaper = useStopPaperRun();
  const { data: runs } = useStrategyRuns(strategyId);
  const activePaperRun = runs?.find(
    (r) => r.mode === "PAPER" && ACTIVE_RUN_STATUSES.includes(r.status),
  );

  // Editor state is seeded from the loaded strategy and edited locally until
  // "Save" pushes a PATCH (which bumps the version).
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [spec, setSpec] = useState<StrategySpec | null>(null);
  const [seeded, setSeeded] = useState<string | null>(null);

  if (strategy && seeded !== strategy.id) {
    setName(strategy.name);
    setDescription(strategy.description ?? "");
    setSpec(strategy.spec);
    setSeeded(strategy.id);
  }

  if (isLoading) return <div className="muted">Loading…</div>;
  if (error) return <div className="error-text">{apiErrorMessage(error)}</div>;
  if (!strategy || !spec) return <div className="muted">Strategy not found.</div>;

  const dirty =
    name !== strategy.name ||
    description !== (strategy.description ?? "") ||
    JSON.stringify(spec) !== JSON.stringify(strategy.spec);

  function save() {
    update.mutate({
      name,
      description: description || null,
      // spec is guaranteed non-null by the early return above.
      spec: spec!,
    });
  }

  function startRun() {
    if (!activeAccountId) return;
    startPaper.mutate({ account_id: activeAccountId });
  }

  return (
    <div className="col">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div className="col" style={{ gap: 4 }}>
          <Link to="/strategies" className="muted" style={{ fontSize: 13 }}>
            ← Strategies
          </Link>
          <h2 style={{ margin: 0 }}>{strategy.name}</h2>
          <div className="muted" style={{ fontSize: 13 }}>
            {strategy.spec_summary ?? "-"} · v{strategy.version}
          </div>
        </div>
        <div className="row">
          <button
            className="danger"
            onClick={() => {
              if (confirm("Delete this strategy?")) {
                del.mutate(strategy.id, {
                  onSuccess: () => navigate("/strategies"),
                });
              }
            }}
          >
            Delete
          </button>
        </div>
      </div>

      {/* Editor */}
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <StrategyEditor
          strategy={strategy}
          templates={templates ?? []}
          name={name}
          description={description}
          spec={spec}
          onName={setName}
          onDescription={setDescription}
          onSpec={setSpec}
        />
        <div className="row">
          <button onClick={save} disabled={!dirty || update.isPending}>
            {update.isPending ? "Saving…" : "Save (new version)"}
          </button>
          {update.error && (
            <span className="error-text">{apiErrorMessage(update.error)}</span>
          )}
          {update.isSuccess && !dirty && <span className="pos">Saved</span>}
        </div>
        <div className="muted" style={{ fontSize: 12 }}>
          Saving creates a new version. Active runs keep running on their pinned
          version until stopped and restarted.
        </div>
      </div>

      {/* Live paper run controls */}
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <strong>Live paper run</strong>
        {activePaperRun ? (
          <div className="row">
            <span className="badge sim">RUNNING</span>
            <span className="muted" style={{ fontSize: 13 }}>
              v{activePaperRun.strategy_version} · account {activePaperRun.account_id?.slice(0, 8)}
            </span>
            <button
              className="secondary"
              onClick={() => stopPaper.mutate(activePaperRun.id)}
              disabled={stopPaper.isPending}
            >
              {stopPaper.isPending ? "Stopping…" : "Stop"}
            </button>
          </div>
        ) : (
          <div className="row">
            {activeAccountId ? (
              <>
                <span className="muted" style={{ fontSize: 13 }}>
                  Active account: {activeAccountId.slice(0, 8)}…
                </span>
                <button onClick={startRun} disabled={startPaper.isPending}>
                  {startPaper.isPending ? "Starting…" : "Start paper run"}
                </button>
                {startPaper.error && (
                  <span className="error-text">{apiErrorMessage(startPaper.error)}</span>
                )}
              </>
            ) : (
              <span className="muted">Select a paper account to start a live run.</span>
            )}
          </div>
        )}
        <div className="muted" style={{ fontSize: 12 }}>
          Paper runs poll for new bars (default every 30 min) and place MARKET
          or LIMIT signals on your account, attributed to this strategy. Stopping
          cancels resting LIMIT orders; it never closes open positions.
        </div>
      </div>

      <BacktestPanel strategyId={strategyId} />
      <RunHistory strategyId={strategyId} />
    </div>
  );
}
