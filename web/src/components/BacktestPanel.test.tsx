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

describe("BacktestPanel v2 records", () => {
  const V2_RUN = {
    ...COMPLETED_RUN,
    stats: {
      ...COMPLETED_RUN.stats,
      annual_return: 0.21,
      annual_volatility: 0.18,
      sortino: 1.9,
      calmar: 2.4,
      turnover: 0.35,
    },
    cost_config: {
      enabled: true,
      commission_rate: "0.00025",
      stamp_tax_rate: "0.0005",
      slippage_bps: "5",
    },
    cost_summary: {
      commission: "8.86",
      stamp_tax: "9.70",
      slippage: "17.72",
      total: "18.56",
      fills: 6,
    },
    benchmark_config: ["000300.SS", "000905.SS"],
    benchmark_results: [
      {
        symbol: "000300.SS",
        available: true,
        reason: null,
        points: [["2026-01-01", "4000"], ["2026-02-01", "4100"]],
        stats: { total_return: 0.025, volatility: 0.14, max_drawdown: -0.02 },
        relative: {
          excess_return: 0.095,
          tracking_error: 0.03,
          information_ratio: 1.1,
          aligned_points: 20,
        },
      },
      {
        symbol: "000905.SS",
        available: false,
        reason: "no bar data for benchmark 000905.SS in 2026-01-01..2026-02-01",
        points: null,
        stats: null,
        relative: null,
      },
    ],
    walkforward_windows: [
      {
        window: 1,
        train_start: "2026-01-01",
        train_end: "2026-03-01",
        test_start: "2026-03-02",
        test_end: "2026-04-01",
        stats: { total_return: 0.03, trade_count: 2 },
      },
    ],
    param_grid: { fast: [5, 10] },
    scan_results: [
      { params: { fast: 5 }, stats: { total_return: 0.08, sharpe: 1.2, max_drawdown: -0.03, trade_count: 4 } },
    ],
    in_sample: true,
  };

  beforeEach(() => {
    mocks.api.get.mockReset();
    mocks.api.post.mockReset();
  });

  async function renderWithRun(run: unknown) {
    mocks.api.get.mockImplementation((url: string) => {
      if (url.includes("/strategies/")) return Promise.resolve({ data: STRATEGY });
      return Promise.resolve({ data: run });
    });
    mocks.api.post.mockResolvedValue({ data: { id: "run1" } });
    renderWithProviders(<BacktestPanel strategyId="s1" />);
    fireEvent.click(screen.getByRole("button", { name: /run backtest/i }));
    await waitFor(() => expect(screen.getByText("COMPLETED")).toBeDefined());
  }

  it("renders the extended stats returned by the API", async () => {
    await renderWithRun(V2_RUN);
    expect(screen.getByText("Annual return")).toBeDefined();
    expect(screen.getByText("Sortino")).toBeDefined();
    expect(screen.getByText("Calmar")).toBeDefined();
  });

  it("renders the cost summary when the cost model was enabled", async () => {
    await renderWithRun(V2_RUN);
    expect(screen.getByText("Costs")).toBeDefined();
    expect(screen.getByText("Commission")).toBeDefined();
    expect(screen.getByText("Stamp tax")).toBeDefined();
  });

  it("renders an unavailable benchmark with its reason instead of omitting it", async () => {
    await renderWithRun(V2_RUN);
    expect(screen.getByText("Benchmarks")).toBeDefined();
    // 缺失的基准必须以原因出现，不能整行消失
    expect(
      screen.getByText(/no bar data for benchmark 000905\.SS/),
    ).toBeDefined();
  });

  it("renders walk-forward windows and the in-sample scan disclaimer", async () => {
    await renderWithRun(V2_RUN);
    expect(screen.getByText("Walk-forward windows")).toBeDefined();
    expect(screen.getByText("Parameter scan")).toBeDefined();
    expect(screen.getByText(/not a recommendation/)).toBeDefined();
  });

  it("omits the v2 sections entirely on a run that has none", async () => {
    await renderWithRun(COMPLETED_RUN);
    expect(screen.queryByText("Costs")).toBeNull();
    expect(screen.queryByText("Benchmarks")).toBeNull();
    expect(screen.queryByText("Walk-forward windows")).toBeNull();
    expect(screen.queryByText("Parameter scan")).toBeNull();
  });
});
