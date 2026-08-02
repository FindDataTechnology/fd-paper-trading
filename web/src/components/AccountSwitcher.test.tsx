import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import AccountSwitcher from "./AccountSwitcher";
import { renderWithProviders } from "../test-utils";
import { useUI } from "../store/ui";

const mocks = vi.hoisted(() => ({ api: { get: vi.fn(), post: vi.fn() } }));

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

const account = (id: string, name: string, venue: string) => ({
  id,
  name,
  venue,
  currency: "USD",
  starting_balance: "100000",
  cash_balance: "100000",
  created_at: "",
});

beforeEach(() => {
  mocks.api.get.mockReset();
  mocks.api.post.mockReset();
  useUI.setState({ activeAccountId: null });
});

describe("AccountSwitcher", () => {
  it("lists accounts, auto-selects the first, and switches the active account", async () => {
    mocks.api.get.mockResolvedValue({
      data: [account("a1", "acct1", "PAPER-A1"), account("a2", "acct2", "PAPER-A2")],
    });

    renderWithProviders(<AccountSwitcher />);
    await waitFor(() => expect(useUI.getState().activeAccountId).toBe("a1"));
    expect(screen.getByText(/Account/)).toBeInTheDocument();

    fireEvent.change(screen.getByRole("combobox"), { target: { value: "a2" } });
    expect(useUI.getState().activeAccountId).toBe("a2");
  });

  it("offers to create an account when none exist", async () => {
    mocks.api.get.mockResolvedValue({ data: [] });
    mocks.api.post.mockResolvedValue({ data: { id: "a1" } });

    renderWithProviders(<AccountSwitcher />);
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Create account/i })).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: /Create account/i }));
    await waitFor(() =>
      expect(mocks.api.post).toHaveBeenCalledWith("/accounts", { name: "default" })
    );
  });
});
