import { beforeEach, describe, expect, it, vi } from "vitest";
import { Routes, Route, useParams } from "react-router-dom";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import SymbolSearch from "./SymbolSearch";
import { renderWithProviders } from "../test-utils";

const mocks = vi.hoisted(() => ({ api: { get: vi.fn() } }));

vi.mock("../api/client", () => ({
  api: mocks.api,
  apiErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { error?: { message?: string } } } };
    return err?.response?.data?.error?.message ?? "Request failed";
  },
}));

vi.mock("../realtime/useRealtime", () => ({
  useQuote: () => undefined,
  useOrderEvents: () => undefined,
  useRealtime: (selector: (s: { quotes: Record<string, unknown> }) => unknown) =>
    selector({ quotes: {} }),
}));

function InstrumentRoute() {
  const { symbol } = useParams();
  return <div>instrument-{symbol}</div>;
}

beforeEach(() => mocks.api.get.mockReset());

describe("SymbolSearch", () => {
  it("filters results from the API and navigates on select", async () => {
    mocks.api.get.mockResolvedValue({
      data: [{ symbol: "AAPL", name: "Apple Inc.", venue: "SIM" }],
    });

    renderWithProviders(
      <Routes>
        <Route path="/" element={<SymbolSearch />} />
        <Route path="/instruments/:symbol" element={<InstrumentRoute />} />
      </Routes>,
      { route: "/" }
    );

    const input = screen.getByPlaceholderText("Search symbol…");
    fireEvent.change(input, { target: { value: "AA" } });

    // Debounce is 250ms; wait for the result to render.
    await waitFor(
      () => expect(screen.getByText("Apple Inc.")).toBeInTheDocument(),
      { timeout: 2000 }
    );

    fireEvent.mouseDown(screen.getByText("AAPL"));
    await waitFor(() =>
      expect(screen.getByText("instrument-AAPL")).toBeInTheDocument()
    );
  });
});
