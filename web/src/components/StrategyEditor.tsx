import { useMemo } from "react";
import type {
  JsonSchemaProp,
  StrategyOut,
  StrategySpec,
  StrategyTemplateOut,
} from "../api/types";
import WeightEditor from "./WeightEditor";

/**
 * Render a minimal set of inputs from a template's JSON Schema (params_schema).
 * Supports the scalar types the template catalog uses: integer / number /
 * string. Defaults come from the schema; values are coerced to the declared
 * type before being emitted. Unknown/complex types fall back to a text input.
 */
function propDefault(prop: JsonSchemaProp): string | number {
  const d = prop.default;
  if (typeof d === "number") return d;
  if (typeof d === "string") return d;
  // No default: use the schema minimum as a sensible floor for numeric types
  // (e.g. quantity has minimum:1 and no default, so defaulting to 0 would
  // fail validation).
  if (
    prop.minimum !== undefined &&
    (prop.type === "integer" || prop.type === "number")
  ) {
    return prop.minimum;
  }
  switch (prop.type) {
    case "integer":
    case "number":
      return 0;
    default:
      return "";
  }
}

function coerce(value: string, prop: JsonSchemaProp): string | number {
  if (prop.type === "integer") {
    const n = parseInt(value, 10);
    return isNaN(n) ? 0 : n;
  }
  if (prop.type === "number") {
    const n = parseFloat(value);
    return isNaN(n) ? 0 : n;
  }
  return value;
}

export interface StrategyEditorProps {
  /** Existing strategy (edit mode) or null (create mode). */
  strategy?: StrategyOut | null;
  templates: StrategyTemplateOut[];
  /** Group limit on the weight track's universe size (defaults to the API default). */
  universeLimit?: number;
  /** Controlled current values. */
  name: string;
  description: string;
  spec: StrategySpec;
  /** Change handlers (parent owns state). */
  onName: (v: string) => void;
  onDescription: (v: string) => void;
  onSpec: (s: StrategySpec) => void;
}

export default function StrategyEditor({
  strategy,
  templates,
  universeLimit = 20,
  name,
  description,
  spec,
  onName,
  onDescription,
  onSpec,
}: StrategyEditorProps) {
  const kind = spec.kind;

  // The weight track has no single symbol; the other two require one. Keeping the
  // last known symbol lets a switch to template/code carry it over instead of
  // blanking a field the author already filled in.
  const currentSymbol = kind === "weight" ? "" : spec.symbol;

  // The template currently selected (for rendering its param form).
  const template = useMemo(
    () =>
      kind === "template"
        ? templates.find((t) => t.id === spec.template) ?? null
        : null,
    [kind, spec, templates],
  );

  function setTemplate(id: string) {
    const t = templates.find((x) => x.id === id);
    if (!t) return;
    // Seed params from schema defaults.
    const params: Record<string, string | number> = {};
    const props = t.params_schema.properties ?? {};
    for (const [key, prop] of Object.entries(props)) {
      params[key] = propDefault(prop);
    }
    onSpec({ kind: "template", template: id, symbol: currentSymbol, params });
  }

  function setParam(key: string, raw: string) {
    if (kind !== "template" || !template) return;
    const prop = template.params_schema.properties?.[key];
    if (!prop) return;
    onSpec({
      ...spec,
      params: { ...(spec.params ?? {}), [key]: coerce(raw, prop) },
    });
  }

  function setKind(next: "template" | "code" | "weight") {
    if (next === "template" && kind !== "template") {
      const first = templates[0];
      if (first) setTemplate(first.id);
      else onSpec({ kind: "template", template: "", symbol: currentSymbol, params: {} });
    } else if (next === "code" && kind !== "code") {
      onSpec({
        kind: "code",
        symbol: currentSymbol,
        source: strategy?.spec?.kind === "code" ? strategy.spec.source : "",
      });
    } else if (next === "weight" && kind !== "weight") {
      // Switching to the weight track keeps nothing from a single-symbol spec
      // except any source the author already wrote — the universe and cadence are
      // new, and a symbol has no meaning here.
      onSpec({
        kind: "weight",
        universe: [],
        rebalance: { every_n_bars: 5 },
        source: strategy?.spec?.kind === "code" ? strategy.spec.source : "",
        weights_series: null,
      });
    }
  }

  return (
    <div className="col">
      <label className="col" style={{ gap: 4 }}>
        <span className="muted">Name</span>
        <input
          value={name}
          onChange={(e) => onName(e.target.value)}
          placeholder="My SMA cross strategy"
          aria-label="strategy name"
        />
      </label>

      <label className="col" style={{ gap: 4 }}>
        <span className="muted">Description</span>
        <input
          value={description}
          onChange={(e) => onDescription(e.target.value)}
          placeholder="optional"
          aria-label="strategy description"
        />
      </label>

      <div className="row">
        <label className="muted" style={{ gap: 4 }}>
          Type
          <select
            value={kind}
            onChange={(e) => setKind(e.target.value as "template" | "code" | "weight")}
            aria-label="strategy type"
          >
            <option value="template">Template</option>
            <option value="code">Custom code</option>
            <option value="weight">Weight portfolio</option>
          </select>
        </label>

        {kind !== "weight" && (
          <label className="col" style={{ gap: 4 }}>
            <span className="muted">Symbol</span>
            <input
              value={spec.symbol}
              onChange={(e) => onSpec({ ...spec, symbol: e.target.value.toUpperCase() })}
              placeholder="AAPL"
              aria-label="symbol"
              style={{ width: 100 }}
            />
          </label>
        )}
      </div>

      {kind === "template" && (
        <div className="col">
          <label className="col" style={{ gap: 4 }}>
            <span className="muted">Template</span>
            <select
              value={spec.template}
              onChange={(e) => setTemplate(e.target.value)}
              aria-label="template"
            >
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          {template && (
            <div className="muted" style={{ fontSize: 13 }}>
              {template.description}
            </div>
          )}
          {template && (
            <div className="grid" style={{ gridTemplateColumns: "repeat(2, minmax(0, 1fr))" }}>
              {Object.entries(template.params_schema.properties ?? {}).map(
                ([key, prop]) => {
                  const required =
                    template.params_schema.required?.includes(key) ?? false;
                  const val = spec.params?.[key];
                  return (
                    <label key={key} className="col" style={{ gap: 4 }}>
                      <span className="muted">
                        {key}
                        {required ? " *" : ""}
                      </span>
                      <input
                        type={
                          prop.type === "integer" || prop.type === "number"
                            ? "number"
                            : "text"
                        }
                        step={prop.type === "number" ? "any" : undefined}
                        min={prop.minimum}
                        max={prop.maximum}
                        value={val === undefined ? "" : String(val)}
                        onChange={(e) => setParam(key, e.target.value)}
                        aria-label={`param ${key}`}
                      />
                      {prop.description && (
                        <span className="muted" style={{ fontSize: 12 }}>
                          {prop.description}
                        </span>
                      )}
                    </label>
                  );
                },
              )}
            </div>
          )}
        </div>
      )}

      {kind === "code" && (
        <div className="col">
          <span className="muted">Source (Python)</span>
          <textarea
            value={spec.source}
            onChange={(e) =>
              onSpec({ kind: "code", symbol: currentSymbol, source: e.target.value })
            }
            rows={14}
            spellCheck={false}
            aria-label="strategy source code"
            style={{
              fontFamily: "monospace",
              fontSize: 13,
              background: "var(--bg)",
              color: "var(--text)",
              border: "1px solid var(--border)",
              borderRadius: 4,
              padding: 8,
            }}
            placeholder={"def on_bar(ctx):\n    # ctx.closes(), ctx.sma(n), ctx.macd(fast,slow,signal)\n    # ctx.bollinger(period, num_std)\n    if ctx.closes()[-1] > ctx.sma(20)[-1]:\n        ctx.buy(1)\n"}
          />
          <span className="muted" style={{ fontSize: 12 }}>
            Custom code runs in a sandboxed subprocess (no imports, safe builtins
            only). Define a top-level <code>on_bar(ctx)</code>.
          </span>
        </div>
      )}

      {kind === "weight" && (
        <WeightEditor spec={spec} onSpec={onSpec} universeLimit={universeLimit} />
      )}
    </div>
  );
}

/** Initial blank spec for the create flow. */
export function blankSpec(templates: StrategyTemplateOut[]): StrategySpec {
  const first = templates[0];
  if (!first) {
    return { kind: "code", symbol: "", source: "" };
  }
  const params: Record<string, string | number> = {};
  for (const [key, prop] of Object.entries(first.params_schema.properties ?? {})) {
    params[key] = propDefault(prop);
  }
  return { kind: "template", template: first.id, symbol: "", params };
}
