import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../test-utils";
import { useUI } from "../store/ui";
import MobileApp from "./MobileApp";

vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), patch: vi.fn() },
  apiErrorMessage: () => "Request failed",
}));

vi.mock("../realtime/useRealtime", () => ({
  useRealtime: (sel: (s: { quotes: Record<string, unknown>; subscribe: () => () => void }) => unknown) =>
    sel({ quotes: {}, subscribe: () => () => undefined }),
  useQuote: () => undefined,
  useOrderEvents: () => undefined,
  useStrategyEvents: () => undefined,
}));

const hooks = vi.hoisted(() => ({
  useAccounts: vi.fn(),
  useAccountSummary: vi.fn(),
  usePositions: vi.fn(),
  useOrders: vi.fn(),
  useInstruments: vi.fn(),
  useBars: vi.fn(),
  useStrategies: vi.fn(),
  useStrategyRuns: vi.fn(),
  usePlaceOrder: vi.fn(),
  useCancelOrder: vi.fn(),
  useStartPaperRun: vi.fn(),
  useStopPaperRun: vi.fn(),
}));

vi.mock("../api/hooks", () => hooks);

const noopMutation = { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false };

beforeEach(() => {
  useUI.setState({ activeAccountId: "a1" });
  hooks.useAccounts.mockReturnValue({
    data: [
      { id: "a1", name: "acct1", venue: "PAPER", currency: "USD", starting_balance: "100000", cash_balance: "10000", created_at: "" },
    ],
  });
  hooks.useAccountSummary.mockReturnValue({
    data: { id: "a1", name: "acct1", venue: "PAPER", currency: "USD", cash_balance: "10000", equity: "12000", open_positions: 0, open_orders: 0, created_at: "" },
  });
  hooks.usePositions.mockReturnValue({ data: [] });
  hooks.useOrders.mockReturnValue({ data: [] });
  hooks.useInstruments.mockReturnValue({ data: [] });
  hooks.useBars.mockReturnValue({ data: [] });
  hooks.useStrategies.mockReturnValue({ data: [] });
  hooks.useStrategyRuns.mockReturnValue({ data: [] });
  hooks.usePlaceOrder.mockReturnValue(noopMutation);
  hooks.useCancelOrder.mockReturnValue(noopMutation);
  hooks.useStartPaperRun.mockReturnValue(noopMutation);
  hooks.useStopPaperRun.mockReturnValue(noopMutation);
});

describe("MobileApp", () => {
  it("renders the shell with all five tabs and the equity summary", () => {
    renderWithProviders(<MobileApp />);
    expect(screen.getByText("fd-paper-trading")).toBeInTheDocument();
    expect(screen.getByText("12,000.00")).toBeInTheDocument();
    for (const label of ["Watch", "Trade", "Pos", "Orders", "Strat"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("switches tabs on tap and shows the positions empty state", () => {
    renderWithProviders(<MobileApp />);
    // Watch is the default tab; switch to Positions.
    fireEvent.click(screen.getByText("Pos"));
    expect(screen.getByText(/No open positions/i)).toBeInTheDocument();
  });

  it("switches to the Strategies tab empty state", () => {
    renderWithProviders(<MobileApp />);
    fireEvent.click(screen.getByText("Strat"));
    expect(screen.getByText(/No strategies yet/i)).toBeInTheDocument();
  });

  it("offers a link back to the desktop view", () => {
    renderWithProviders(<MobileApp />);
    expect(screen.getByText("Desktop").closest("a")).toHaveAttribute("href", "/");
  });
});
