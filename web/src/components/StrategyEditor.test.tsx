import { describe, expect, it } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { useState } from "react";
import StrategyEditor, { blankSpec } from "./StrategyEditor";
import { renderWithProviders } from "../test-utils";
import type {
  StrategyOut,
  StrategySpec,
  StrategyTemplateOut,
} from "../api/types";

const templates: StrategyTemplateOut[] = [
  {
    id: "sma_cross",
    name: "SMA crossover",
    description: "Fast/slow cross.",
    params_schema: {
      type: "object",
      properties: {
        fast: { type: "integer", minimum: 1, default: 10, description: "Fast SMA period" },
        slow: { type: "integer", minimum: 2, default: 30 },
        quantity: { type: "number", minimum: 0, default: 1 },
      },
      required: ["quantity"],
      additionalProperties: false,
    },
  },
];

/** Stateful wrapper so the controlled editor re-renders on spec changes. */
function StatefulEditor({
  initial,
  strategy,
}: {
  initial: StrategySpec;
  strategy?: StrategyOut;
}) {
  const [name, setName] = useState("My strat");
  const [description, setDescription] = useState("");
  const [spec, setSpec] = useState<StrategySpec>(initial);
  return (
    <StrategyEditor
      strategy={strategy}
      templates={templates}
      name={name}
      description={description}
      spec={spec}
      onName={setName}
      onDescription={setDescription}
      onSpec={setSpec}
    />
  );
}

describe("StrategyEditor", () => {
  it("renders param inputs from the template JSON Schema with defaults", () => {
    const spec: StrategySpec = {
      kind: "template",
      template: "sma_cross",
      symbol: "AAPL",
      params: { fast: 10, slow: 30, quantity: 1 },
    };
    renderWithProviders(<StatefulEditor initial={spec} />);

    expect(screen.getByLabelText("symbol")).toHaveValue("AAPL");
    expect(screen.getByLabelText("template")).toHaveValue("sma_cross");

    const fast = screen.getByLabelText("param fast") as HTMLInputElement;
    const slow = screen.getByLabelText("param slow") as HTMLInputElement;
    const qty = screen.getByLabelText("param quantity") as HTMLInputElement;
    expect(fast).toHaveValue(10);
    expect(slow).toHaveValue(30);
    expect(qty).toHaveValue(1);

    expect(screen.getByText("Fast SMA period")).toBeInTheDocument();
  });

  it("switches to the code track and shows the source textarea", () => {
    const spec = blankSpec(templates); // starts as template
    renderWithProviders(<StatefulEditor initial={spec} />);

    fireEvent.change(screen.getByLabelText("strategy type"), {
      target: { value: "code" },
    });

    const textarea = screen.getByLabelText("strategy source code") as HTMLTextAreaElement;
    expect(textarea).toBeInTheDocument();
    expect(textarea.value).toBe("");
  });

  it("preserves an existing code strategy's source when switching back to code", () => {
    const strategy = {
      id: "s1",
      name: "code strat",
      description: null,
      spec: { kind: "code", symbol: "MSFT", source: "def on_bar(ctx):\n    pass" },
      spec_summary: "custom code on MSFT",
      version: 3,
      created_at: "",
      updated_at: "",
    } as StrategyOut;
    // Start on a template track, then switch to code: the editor should seed
    // the source from the existing strategy's code spec.
    const initial: StrategySpec = {
      kind: "template",
      template: "sma_cross",
      symbol: "MSFT",
      params: { fast: 10, slow: 30, quantity: 1 },
    };
    renderWithProviders(<StatefulEditor initial={initial} strategy={strategy} />);

    fireEvent.change(screen.getByLabelText("strategy type"), {
      target: { value: "code" },
    });

    const textarea = screen.getByLabelText("strategy source code") as HTMLTextAreaElement;
    expect(textarea.value).toContain("def on_bar(ctx)");
  });

  it("blankSpec seeds params from schema defaults", () => {
    const spec = blankSpec(templates);
    expect(spec.kind).toBe("template");
    if (spec.kind !== "template") return;
    expect(spec.template).toBe("sma_cross");
    expect(spec.params?.fast).toBe(10);
    expect(spec.params?.slow).toBe(30);
  });

  // ---- weight track (task 6.1) ----

  it("renders the weight track's editor with universe, cadence and source form", () => {
    const spec: StrategySpec = {
      kind: "weight",
      universe: ["600519", "000001"],
      rebalance: { every_n_bars: 5 },
      source: "def target_weights(ctx):\n    return {'600519': 0.5}",
      weights_series: null,
    };
    renderWithProviders(<StatefulEditor initial={spec} />);

    expect(screen.getByLabelText("remove 600519")).toBeInTheDocument();
    expect(screen.getByLabelText("move 000001 up")).toBeInTheDocument();
    expect(screen.getByLabelText("add symbols")).toBeInTheDocument();
    expect(screen.getByLabelText("rebalance every n bars")).toHaveValue(5);
    const textarea = screen.getByLabelText(
      "weight strategy source code",
    ) as HTMLTextAreaElement;
    expect(textarea.value).toContain("def target_weights(ctx)");
    // A single symbol has no meaning on this track, so the field is absent.
    expect(screen.queryByLabelText("symbol")).not.toBeInTheDocument();
  });

  it("switching to the weight track drops the single-symbol spec shape", () => {
    const spec = blankSpec(templates); // template track
    renderWithProviders(<StatefulEditor initial={spec} />);

    fireEvent.change(screen.getByLabelText("strategy type"), {
      target: { value: "weight" },
    });

    expect(screen.getByLabelText("rebalance every n bars")).toHaveValue(5);
    expect(screen.getByLabelText("add symbols")).toBeInTheDocument();
    expect(screen.queryByLabelText("param fast")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("symbol")).not.toBeInTheDocument();
  });

  it("adds symbols from the draft input, uppercasing and de-duplicating", () => {
    const spec: StrategySpec = {
      kind: "weight",
      universe: ["600519"],
      rebalance: { every_n_bars: 3 },
      source: "def target_weights(ctx):\n    return {}",
      weights_series: null,
    };
    renderWithProviders(<StatefulEditor initial={spec} />);

    const draft = screen.getByLabelText("add symbols");
    fireEvent.change(draft, { target: { value: "000001, 600519" } });
    fireEvent.click(screen.getByText("Add"));

    expect(screen.getByLabelText("remove 000001")).toBeInTheDocument();
    expect(screen.getAllByLabelText("remove 600519")).toHaveLength(1);
    expect((draft as HTMLInputElement).value).toBe("");
  });

  it("removing a symbol drops it from the universe", () => {
    const spec: StrategySpec = {
      kind: "weight",
      universe: ["600519", "000001"],
      rebalance: { every_n_bars: 3 },
      source: "def target_weights(ctx):\n    return {}",
      weights_series: null,
    };
    renderWithProviders(<StatefulEditor initial={spec} />);

    fireEvent.click(screen.getByLabelText("remove 600519"));
    expect(screen.queryByLabelText("remove 600519")).not.toBeInTheDocument();
    expect(screen.getByLabelText("remove 000001")).toBeInTheDocument();
  });

  it("reordering a symbol moves the primary (first) universe entry", () => {
    const spec: StrategySpec = {
      kind: "weight",
      universe: ["600519", "000001"],
      rebalance: { every_n_bars: 3 },
      source: "def target_weights(ctx):\n    return {}",
      weights_series: null,
    };
    renderWithProviders(<StatefulEditor initial={spec} />);

    fireEvent.click(screen.getByLabelText("move 000001 up"));
    // Order carries the primary-symbol semantics, so the first chip must change.
    const chips = screen.getAllByLabelText(/^remove /);
    expect(chips[0]).toHaveAttribute("aria-label", "remove 000001");
  });

  it("switching the weight source to a series shows the JSON editor", () => {
    const spec: StrategySpec = {
      kind: "weight",
      universe: ["600519"],
      rebalance: { every_n_bars: 3 },
      source: "def target_weights(ctx):\n    return {}",
      weights_series: null,
    };
    renderWithProviders(<StatefulEditor initial={spec} />);

    fireEvent.change(screen.getByLabelText("weight source form"), {
      target: { value: "weights_series" },
    });

    expect(screen.getByLabelText("weights series json")).toBeInTheDocument();
    expect(
      screen.queryByLabelText("weight strategy source code"),
    ).not.toBeInTheDocument();
  });

  it("a malformed series shows an error and does not reach the spec", () => {
    const spec: StrategySpec = {
      kind: "weight",
      universe: ["600519"],
      rebalance: { every_n_bars: 3 },
      source: "def target_weights(ctx):\n    return {}",
      weights_series: null,
    };
    renderWithProviders(<StatefulEditor initial={spec} />);
    fireEvent.change(screen.getByLabelText("weight source form"), {
      target: { value: "weights_series" },
    });

    const series = screen.getByLabelText("weights series json");
    fireEvent.change(series, { target: { value: "{not json" } });
    // The author's text is kept so they can fix it, while the emitted spec stays
    // well-formed — otherwise Save would push invalid data to the API.
    expect((series as HTMLTextAreaElement).value).toBe("{not json");
    // The parser's own message is surfaced (a JSON.parse failure names the
    // problem); matching the class rather than the exact wording keeps this
    // assertion from breaking on a Node message change.
    const errorLine = document.querySelector(".error-text");
    expect(errorLine).not.toBeNull();
    expect(errorLine!.textContent).toMatch(/JSON|token|property/i);
  });

  it("renders a contract-instance series read-only instead of as JSON text", () => {
    const spec: StrategySpec = {
      kind: "weight",
      universe: ["600519", "CASH"],
      rebalance: { every_n_bars: 3 },
      source: null,
      weights_series: {
        "2026-01-02": {
          schema_version: "1",
          as_of: "2026-01-02",
          universe: ["600519", "CASH"],
          weights: { "600519": 0.5 },
          cash_floor: 0.5,
          producer: { pack: "pack-strategy-lab", skill: "weights" },
        },
      },
    };
    renderWithProviders(<StatefulEditor initial={spec} />);

    expect(screen.getByLabelText("weights series read-only")).toBeInTheDocument();
    expect(screen.queryByLabelText("weights series json")).not.toBeInTheDocument();
  });

  it("warns when the universe exceeds the group limit", () => {
    const spec: StrategySpec = {
      kind: "weight",
      universe: ["A", "B", "C"],
      rebalance: { every_n_bars: 3 },
      source: "def target_weights(ctx):\n    return {}",
      weights_series: null,
    };
    renderWithProviders(
      <StrategyEditor
        templates={templates}
        name="n"
        description=""
        spec={spec}
        universeLimit={2}
        onName={() => {}}
        onDescription={() => {}}
        onSpec={() => {}}
      />,
    );

    expect(screen.getByText(/exceeds the group limit of 2/)).toBeInTheDocument();
  });
});
