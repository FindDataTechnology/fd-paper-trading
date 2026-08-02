import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import OrderTicket from "./OrderTicket";
import { renderWithProviders } from "../test-utils";

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

beforeEach(() => {
  mocks.api.get.mockReset();
  mocks.api.post.mockReset();
  mocks.api.delete.mockReset();
  mocks.api.get.mockResolvedValue({
    data: { id: "acc1", cash_balance: "100000", equity: "100000" },
  });
});

describe("OrderTicket", () => {
  it("disables submit and makes no API call when quantity is empty", () => {
    renderWithProviders(<OrderTicket symbol="AAPL" accountId="acc1" />);
    const button = screen.getByRole("button", { name: /BUY AAPL/ });
    expect(button).toBeDisabled();
    expect(mocks.api.post).not.toHaveBeenCalled();
  });

  it("submits a well-formed market order with the right payload", async () => {
    mocks.api.post.mockResolvedValue({ data: { id: "o1", status: "FILLED" } });
    const { container } = renderWithProviders(
      <OrderTicket symbol="AAPL" accountId="acc1" />
    );
    const qtyInput = container.querySelector('input[inputmode="decimal"]');
    expect(qtyInput).not.toBeNull();
    fireEvent.change(qtyInput as HTMLInputElement, { target: { value: "10" } });

    const button = screen.getByRole("button", { name: /BUY AAPL/ });
    await waitFor(() => expect(button).not.toBeDisabled());
    fireEvent.click(button);

    await waitFor(() =>
      expect(mocks.api.post).toHaveBeenCalledWith("/orders", {
        account_id: "acc1",
        symbol: "AAPL",
        side: "BUY",
        order_type: "MARKET",
        quantity: "10",
        limit_price: null,
      })
    );
  });
});
