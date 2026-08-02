import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import PositionsTable from "./PositionsTable";
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

beforeEach(() => mocks.api.get.mockReset());

describe("PositionsTable", () => {
  it("renders open positions with quantity and average price", async () => {
    mocks.api.get.mockResolvedValue({
      data: [
        {
          id: "p1",
          instrument_id: "AAPL.SIM",
          symbol: "AAPL",
          quantity: "10",
          avg_price: "150.00",
          realized_pnl: "0",
        },
      ],
    });
    renderWithProviders(<PositionsTable accountId="acc1" />);
    await waitFor(() => expect(screen.getByText("AAPL")).toBeInTheDocument());
    expect(screen.getByText("150.00")).toBeInTheDocument();
  });

  it("shows an empty state when there are no positions", async () => {
    mocks.api.get.mockResolvedValue({ data: [] });
    renderWithProviders(<PositionsTable accountId="acc1" />);
    await waitFor(() => expect(screen.getByText(/No open positions/)).toBeInTheDocument());
  });
});
