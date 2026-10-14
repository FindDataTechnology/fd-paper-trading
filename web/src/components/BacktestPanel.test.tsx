import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import BacktestPanel from "./BacktestPanel";
import { renderWithProviders } from "../test-utils";

// Mock the axios client the hooks depend on. Each test wires api.get/post
// return values; the panel drives the rest through react-query.
const mocks = vi.hoisted(() => ({
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

vi.mock("../api/client", () => ({
  api: mocks.api,
  apiErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { error?: { message?: string } } } };
    return err?.response?.data?.error?.message ?? "Request failed";
  },
}));

// BacktestPanel doesn't touch realtime, but the import chain pulls it in.
vi.mock("../realtime/useRealtime", () => ({
  useStrategyEvents: () => undefined,
}));

const STRATEGY = {
  id: "s1",
  name: "SMA",
  description: null,
  spec: { kind: "template", template: "sma_cross", symbol: "AAPL", params: {} },
  spec_summary: "sma on AAPL",
  version: 1,
  created_at: "",
  updated_at: "",
};

const COMPLETED_RUN = {
  id: "run1",
  strategy_id: "s1",
  strategy_version: 1,
  mode: "BACKTEST",
  account_id: null,
  status: "COMPLETED",
  params: { start: "2026-01-01", end: "2026-02-01" },
  stats: {
    total_return: 0.12,
    max_drawdown: -0.05,
    sharpe: 1.3,
    win_rate: 0.6,
    trade_count: 5,
    final_equity: 112000,
  },
  error: null,
  started_at: null,
  stopped_at: null,
  created_at: "",
  equity_points: [
    { date: "2026-01-01", equity: "100000" },
    { date: "2026-01-02", equity: "105000" },
    { date: "2026-02-01", equity: "112000" },
  ],
  events: [],
};

describe("BacktestPanel", () => {
  beforeEach(() => {
    mocks.api.get.mockReset();
    mocks.api.post.mockReset();
  });

  it("renders date/cash inputs and the fill-at-close note", async () => {
    mocks.api.get.mockResolvedValue({ data: STRATEGY });
    renderWithProviders(<BacktestPanel strategyId="s1" />);
    await waitFor(() => expect(screen.getByLabelText("backtest start")).toBeInTheDocument());
    expect(screen.getByLabelText("backtest end")).toBeInTheDocument();
    expect(screen.getByLabelText("initial cash")).toHaveValue(100000);
    expect(screen.getByText(/Fills are computed at each bar's close/)).toBeInTheDocument();
  });

  it("launches a backtest and renders stats + equity curve on COMPLETED", async () => {
    mocks.api.get.mockImplementation((url: string) => {
      if (url.startsWith("/strategies/")) return Promise.resolve({ data: STRATEGY });
      if (url.startsWith("/strategy-runs/")) return Promise.resolve({ data: COMPLETED_RUN });
      return Promise.resolve({ data: null });
    });
    mocks.api.post.mockResolvedValue({ data: { ...COMPLETED_RUN, status: "QUEUED", stats: null, equity_points: [] } });

    renderWithProviders(<BacktestPanel strategyId="s1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Run backtest" })).toBeEnabled());

    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    // POST fired with the form payload.
    await waitFor(() =>
      expect(mocks.api.post).toHaveBeenCalledWith(
        "/strategies/s1/backtests",
        expect.objectContaining({ initial_cash: "100000" })
      )
    );

    // Stats render from the COMPLETED run.
    await waitFor(() => expect(screen.getByText("Total return")).toBeInTheDocument());
    expect(screen.getByText("12.00%")).toBeInTheDocument();
    expect(screen.getByText("Win rate")).toBeInTheDocument();
    expect(screen.getByText(/Equity curve/)).toBeInTheDocument();
  });

  it("shows the run error message when a run FAILED", async () => {
    const failed = { ...COMPLETED_RUN, status: "FAILED", error: "no bars in window", stats: null, equity_points: [] };
    mocks.api.get.mockImplementation((url: string) => {
      if (url.startsWith("/strategies/")) return Promise.resolve({ data: STRATEGY });
      if (url.startsWith("/strategy-runs/")) return Promise.resolve({ data: failed });
      return Promise.resolve({ data: null });
    });
    mocks.api.post.mockResolvedValue({ data: { ...failed, status: "QUEUED" } });

    renderWithProviders(<BacktestPanel strategyId="s1" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Run backtest" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Run backtest" }));

    await waitFor(() => expect(screen.getByText("no bars in window")).toBeInTheDocument());
  });
});
