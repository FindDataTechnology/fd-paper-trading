import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import OrdersList from "./OrdersList";
import { renderWithProviders } from "../test-utils";
import type { OrderOut } from "../api/types";

const mocks = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

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

const order = (id: string, status: string): OrderOut => ({
  id,
  account_id: "acc1",
  client_order_id: null,
  instrument_id: "AAPL.SIM",
  symbol: "AAPL",
  side: "BUY",
  order_type: "MARKET",
  quantity: "10",
  limit_price: null,
  status,
  filled_quantity: status === "FILLED" ? "10" : "0",
  avg_fill_price: status === "FILLED" ? "150.00" : null,
  reject_reason: null,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
  fills: [],
});

beforeEach(() => {
  mocks.api.get.mockReset();
  mocks.api.delete.mockReset();
});

describe("OrdersList", () => {
  it("renders orders and cancels an active order via the API", async () => {
    mocks.api.get.mockResolvedValue({ data: [order("o1", "OPEN"), order("o2", "FILLED")] });
    mocks.api.delete.mockResolvedValue({ data: order("o1", "CANCELLED") });

    renderWithProviders(<OrdersList accountId="acc1" />);
    await waitFor(() => expect(screen.getAllByText("AAPL")).toHaveLength(2));

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: /Cancel/ }));
    await waitFor(() => expect(mocks.api.delete).toHaveBeenCalledWith("/orders/o1"));
    confirmSpy.mockRestore();
  });

  it("shows an empty state when there are no orders", async () => {
    mocks.api.get.mockResolvedValue({ data: [] });
    renderWithProviders(<OrdersList accountId="acc1" />);
    await waitFor(() =>
      expect(screen.getByText(/No orders for this account/)).toBeInTheDocument()
    );
  });
});
