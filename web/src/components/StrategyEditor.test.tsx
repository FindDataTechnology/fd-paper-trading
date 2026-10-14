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
});
