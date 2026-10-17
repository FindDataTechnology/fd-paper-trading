import { useState } from "react";
import type { WeightSpec } from "../api/types";

/**
 * The weight track's editor: universe, rebalance cadence, and one of the two
 * weight sources.
 *
 * Two forms are authorable here — a Python source defining `target_weights(ctx)`
 * and a static series of dated mappings. The third form the API accepts (a
 * period given as a full TargetWeights contract instance) is deliberately not
 * authorable by hand: those instances are produced by packs, and a text field
 * would invite people to type one wrongly. A spec already holding them renders
 * read-only instead, so the view never silently drops data the author cannot
 * retype.
 */

export interface WeightEditorProps {
  spec: WeightSpec;
  onSpec: (s: WeightSpec) => void;
  /** Group limit for universe size; the API refuses more. */
  universeLimit: number;
}

type SourceForm = "source" | "weights_series";

/** A period is a plain mapping only when every value is a number. */
function isPlainMapping(period: Record<string, unknown>): boolean {
  return Object.values(period).every((v) => typeof v === "number");
}

function parseSeries(text: string): {
  value: Record<string, Record<string, number>> | null;
  error: string | null;
} {
  const trimmed = text.trim();
  if (!trimmed) return { value: null, error: null };
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { value: null, error: "must be a JSON object of date -> weights" };
    }
    return { value: parsed, error: null };
  } catch (e) {
    return { value: null, error: e instanceof Error ? e.message : "invalid JSON" };
  }
}

export default function WeightEditor({ spec, onSpec, universeLimit }: WeightEditorProps) {
  const hasSeries = spec.weights_series != null;
  const [form, setForm] = useState<SourceForm>(hasSeries ? "weights_series" : "source");
  const [seriesText, setSeriesText] = useState(
    hasSeries ? JSON.stringify(spec.weights_series, null, 2) : "",
  );
  const [seriesError, setSeriesError] = useState<string | null>(null);
  const [universeDraft, setUniverseDraft] = useState("");

  const containsContractInstance = Object.values(spec.weights_series ?? {}).some(
    (p) => !isPlainMapping(p as Record<string, unknown>),
  );

  function setUniverse(next: string[]) {
    const cleaned = next.map((s) => s.trim().toUpperCase()).filter(Boolean);
    onSpec({ ...spec, universe: cleaned });
  }

  function addSymbols() {
    const parts = universeDraft
      .split(/[\s,]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    if (!parts.length) return;
    const merged = [...spec.universe];
    for (const p of parts) if (!merged.includes(p)) merged.push(p);
    setUniverse(merged);
    setUniverseDraft("");
  }

  function removeSymbol(sym: string) {
    setUniverse(spec.universe.filter((s) => s !== sym));
  }

  function moveSymbol(index: number, delta: number) {
    const next = [...spec.universe];
    const target = index + delta;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]];
    setUniverse(next);
  }

  function switchForm(next: SourceForm) {
    setForm(next);
    if (next === "source") {
      onSpec({ ...spec, weights_series: null, source: spec.source ?? "" });
    } else {
      onSpec({ ...spec, source: null, weights_series: spec.weights_series ?? {} });
    }
  }

  function onSeriesChange(text: string) {
    setSeriesText(text);
    const { value, error } = parseSeries(text);
    setSeriesError(error);
    // Emit only well-formed JSON: a half-typed series must not be pushed up as
    // the spec, or the parent's save button would send invalid data.
    if (!error) onSpec({ ...spec, weights_series: value });
  }

  const overLimit = spec.universe.length > universeLimit;

  return (
    <div className="col" style={{ gap: 12 }}>
      {/* Universe */}
      <div className="col" style={{ gap: 4 }}>
        <span className="muted">
          Universe ({spec.universe.length}
          {overLimit ? ` / limit ${universeLimit}` : ""})
        </span>
        {overLimit && (
          <span className="error-text" style={{ fontSize: 12 }}>
            {spec.universe.length} symbols exceeds the group limit of {universeLimit};
            a run would be refused.
          </span>
        )}
        <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
          {spec.universe.map((sym, i) => (
            <span key={sym} className="badge" style={{ display: "inline-flex", gap: 4 }}>
              {sym}
              <button
                type="button"
                className="secondary"
                style={{ padding: "0 4px", fontSize: 11 }}
                onClick={() => moveSymbol(i, -1)}
                aria-label={`move ${sym} up`}
              >
                ↑
              </button>
              <button
                type="button"
                className="secondary"
                style={{ padding: "0 4px", fontSize: 11 }}
                onClick={() => moveSymbol(i, 1)}
                aria-label={`move ${sym} down`}
              >
                ↓
              </button>
              <button
                type="button"
                className="danger"
                style={{ padding: "0 4px", fontSize: 11 }}
                onClick={() => removeSymbol(sym)}
                aria-label={`remove ${sym}`}
              >
                ×
              </button>
            </span>
          ))}
          {!spec.universe.length && <span className="muted">no symbols yet</span>}
        </div>
        <div className="row">
          <input
            value={universeDraft}
            onChange={(e) => setUniverseDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                addSymbols();
              }
            }}
            placeholder="600519, 000001"
            aria-label="add symbols"
            style={{ width: 220 }}
          />
          <button type="button" className="secondary" onClick={addSymbols}>
            Add
          </button>
        </div>
        <span className="muted" style={{ fontSize: 12 }}>
          Order matters for live runs: the first symbol is the one whose new bar
          wakes the strategy.
        </span>
      </div>

      {/* Rebalance cadence */}
      <label className="col" style={{ gap: 4, maxWidth: 260 }}>
        <span className="muted">Rebalance every N bars</span>
        <input
          type="number"
          min={1}
          step={1}
          value={spec.rebalance.every_n_bars}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10);
            onSpec({
              ...spec,
              rebalance: { every_n_bars: isNaN(n) ? 1 : n },
            });
          }}
          aria-label="rebalance every n bars"
        />
      </label>

      {/* Source form */}
      <div className="col" style={{ gap: 6 }}>
        <label className="muted" style={{ gap: 4 }}>
          Weight source
          <select
            value={form}
            onChange={(e) => switchForm(e.target.value as SourceForm)}
            aria-label="weight source form"
          >
            <option value="source">Python (target_weights)</option>
            <option value="weights_series">Static weights series</option>
          </select>
        </label>

        {form === "source" && (
          <>
            <textarea
              value={spec.source ?? ""}
              onChange={(e) => onSpec({ ...spec, source: e.target.value })}
              rows={12}
              spellCheck={false}
              aria-label="weight strategy source code"
              style={{
                fontFamily: "monospace",
                fontSize: 13,
                background: "var(--bg)",
                color: "var(--text)",
                border: "1px solid var(--border)",
                borderRadius: 4,
                padding: 8,
              }}
              placeholder={
                "def target_weights(ctx):\n    # ctx.universe, ctx.closes(sym), ctx.current_weights()\n    # Weights must be non-negative and sum to at most 1.\n    return {sym: 1.0 / len(ctx.universe) for sym in ctx.universe}\n"
              }
            />
            <span className="muted" style={{ fontSize: 12 }}>
              Runs in the same sandbox as the code track (no imports, safe builtins).
              Define a top-level <code>target_weights(ctx)</code> returning{" "}
              <code>{"{symbol: weight}"}</code>; weights are validated against the
              TargetWeights contract and never normalized.
            </span>
          </>
        )}

        {form === "weights_series" && (
          <>
            {containsContractInstance ? (
              <div className="col" style={{ gap: 4 }}>
                <span className="muted" style={{ fontSize: 12 }}>
                  This strategy holds TargetWeights contract instances (produced by
                  a pack). Shown read-only — editing one by hand would be a
                  transcription error waiting to happen.
                </span>
                <pre
                  aria-label="weights series read-only"
                  style={{
                    fontFamily: "monospace",
                    fontSize: 12,
                    background: "var(--bg)",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                    padding: 8,
                    overflowX: "auto",
                    margin: 0,
                  }}
                >
                  {JSON.stringify(spec.weights_series, null, 2)}
                </pre>
              </div>
            ) : (
              <>
                <textarea
                  value={seriesText}
                  onChange={(e) => onSeriesChange(e.target.value)}
                  rows={12}
                  spellCheck={false}
                  aria-label="weights series json"
                  style={{
                    fontFamily: "monospace",
                    fontSize: 13,
                    background: "var(--bg)",
                    color: "var(--text)",
                    border: `1px solid ${seriesError ? "var(--danger, #c33)" : "var(--border)"}`,
                    borderRadius: 4,
                    padding: 8,
                  }}
                  placeholder={'{\n  "2026-01-02": {"600519": 0.5, "000001": 0.3}\n}'}
                />
                {seriesError && (
                  <span className="error-text" style={{ fontSize: 12 }}>
                    {seriesError}
                  </span>
                )}
                <span className="muted" style={{ fontSize: 12 }}>
                  ISO date → {"{symbol: weight}"}. A period holds until the next one;
                  before the first period the portfolio stays empty. Weights must be
                  non-negative and sum to at most 1 per period.
                </span>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
