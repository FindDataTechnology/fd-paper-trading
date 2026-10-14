import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  useCreateStrategy,
  useStrategies,
  useStrategyRuns,
  useStrategyTemplates,
} from "../api/hooks";
import { ACTIVE_RUN_STATUSES } from "../api/types";
import type { StrategyCreate, StrategySpec } from "../api/types";
import { apiErrorMessage } from "../api/client";
import { runStatusClass } from "../lib/format";
import StrategyEditor, { blankSpec } from "../components/StrategyEditor";

/** Map strategy_id -> active run status label, from the user's run list. */
function useActiveRunByStrategy(): Record<string, string> {
  const { data: runs } = useStrategyRuns();
  const out: Record<string, string> = {};
  if (!runs) return out;
  for (const r of runs) {
    if (ACTIVE_RUN_STATUSES.includes(r.status)) {
      // Most-recent active run wins (runs are newest-first).
      if (!out[r.strategy_id]) out[r.strategy_id] = r.status;
    }
  }
  return out;
}

export default function StrategiesPage() {
  const { data: strategies, isLoading, error } = useStrategies();
  const { data: templates } = useStrategyTemplates();
  const activeByStrategy = useActiveRunByStrategy();
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  return (
    <div className="col">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Strategies</h2>
        <button onClick={() => setCreating((v) => !v)}>
          {creating ? "Cancel" : "New strategy"}
        </button>
      </div>

      {creating && (
        <CreateStrategyForm
          templates={templates ?? []}
          onCreated={(id) => {
            setCreating(false);
            navigate(`/strategies/${id}`);
          }}
        />
      )}

      {isLoading && <div className="muted">Loading…</div>}
      {error && <div className="error-text">{apiErrorMessage(error)}</div>}

      {strategies && strategies.length === 0 && !creating && (
        <div className="muted">No strategies yet. Create one to get started.</div>
      )}

      {strategies && strategies.length > 0 && (
        <div className="card">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Spec</th>
                <th>Version</th>
                <th>Run status</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((s) => {
                const active = activeByStrategy[s.id];
                return (
                  <tr key={s.id}>
                    <td>
                      <Link to={`/strategies/${s.id}`}>{s.name}</Link>
                    </td>
                    <td className="muted">{s.spec_summary ?? "—"}</td>
                    <td>v{s.version}</td>
                    <td>
                      {active ? (
                        <span className={runStatusClass(active)}>{active}</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function CreateStrategyForm({
  templates,
  onCreated,
}: {
  templates: import("../api/types").StrategyTemplateOut[];
  onCreated: (id: string) => void;
}) {
  const create = useCreateStrategy();
  // Seed the spec only once the template catalog has loaded (it arrives
  // async via useStrategyTemplates); initializing from an empty catalog would
  // fall back to the code track with empty source and fail validation.
  const [spec, setSpec] = useState<StrategySpec | null>(null);
  useEffect(() => {
    if (spec === null && templates.length > 0) {
      setSpec(blankSpec(templates));
    }
  }, [spec, templates]);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  function submit() {
    if (!name.trim() || !spec) return;
    const payload: StrategyCreate = {
      name: name.trim(),
      description: description.trim() || null,
      spec,
    };
    create.mutate(payload, {
      onSuccess: (s) => onCreated(s.id),
    });
  }

  if (!spec) {
    return <div className="muted">Loading templates…</div>;
  }

  return (
    <div className="card" style={{ flexDirection: "column", display: "flex", gap: 12 }}>
      {create.error && (
        <div className="error-text">{apiErrorMessage(create.error)}</div>
      )}
      <StrategyEditor
        templates={templates}
        name={name}
        description={description}
        spec={spec}
        onName={setName}
        onDescription={setDescription}
        onSpec={setSpec}
      />
      <div className="row">
        <button onClick={submit} disabled={create.isPending || !name.trim()}>
          {create.isPending ? "Creating…" : "Create strategy"}
        </button>
      </div>
    </div>
  );
}
