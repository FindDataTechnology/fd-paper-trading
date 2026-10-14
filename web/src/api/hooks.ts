import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { ACTIVE_RUN_STATUSES } from "./types";
import type {
  AccountOut,
  AccountSummary,
  Bar,
  InstrumentSearchResult,
  OrderCreate,
  OrderOut,
  PositionOut,
  StrategyCreate,
  StrategyOut,
  StrategyRunDetailOut,
  StrategyRunOut,
  StrategyTemplateOut,
  StrategyUpdate,
  BacktestRequest,
  PaperRunRequest,
} from "./types";

export const qk = {
  accounts: ["accounts"] as const,
  account: (id: string) => ["account", id] as const,
  positions: (id: string) => ["positions", id] as const,
  orders: (accountId: string, status?: string) =>
    ["orders", accountId, status ?? "all"] as const,
  order: (id: string) => ["order", id] as const,
  search: (q: string) => ["instruments", q] as const,
  bars: (symbol: string, tf: string) => ["bars", symbol, tf] as const,
  strategies: ["strategies"] as const,
  strategy: (id: string) => ["strategy", id] as const,
  templates: ["strategy-templates"] as const,
  runs: (strategyId?: string) =>
    ["strategy-runs", strategyId ?? "all"] as const,
  run: (id: string) => ["strategy-run", id] as const,
};

export function useAccounts() {
  return useQuery({
    queryKey: qk.accounts,
    queryFn: () => api.get<AccountOut[]>("/accounts").then((r) => r.data),
  });
}

export function useAccountSummary(id: string | null) {
  return useQuery({
    queryKey: id ? qk.account(id) : ["account", "none"],
    queryFn: () => api.get<AccountSummary>(`/accounts/${id}`).then((r) => r.data),
    enabled: !!id,
  });
}

export function usePositions(id: string | null) {
  return useQuery({
    queryKey: id ? qk.positions(id) : ["positions", "none"],
    queryFn: () =>
      api.get<PositionOut[]>(`/accounts/${id}/positions`).then((r) => r.data),
    enabled: !!id,
  });
}

export function useOrders(accountId: string | null, status?: string) {
  return useQuery({
    queryKey: accountId ? qk.orders(accountId, status) : ["orders", "none"],
    queryFn: () =>
      api
        .get<OrderOut[]>(`/accounts/${accountId}/orders`, {
          params: status ? { status } : undefined,
        })
        .then((r) => r.data),
    enabled: !!accountId,
  });
}

export function useOrder(id: string | null) {
  return useQuery({
    queryKey: id ? qk.order(id) : ["order", "none"],
    queryFn: () => api.get<OrderOut>(`/orders/${id}`).then((r) => r.data),
    enabled: !!id,
  });
}

export function useInstruments(q: string) {
  return useQuery({
    queryKey: qk.search(q),
    queryFn: () =>
      api
        .get<InstrumentSearchResult[]>("/instruments/search", { params: { q } })
        .then((r) => r.data),
    enabled: q.trim().length >= 2,
  });
}

export function useBars(symbol: string | null, timeframe: string) {
  return useQuery({
    queryKey: symbol ? qk.bars(symbol, timeframe) : ["bars", "none"],
    queryFn: () =>
      api
        .get<Bar[]>(`/instruments/${symbol}/bars`, {
          params: { timeframe, limit: 200 },
        })
        .then((r) => r.data),
    enabled: !!symbol,
  });
}

export function usePlaceOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: OrderCreate) =>
      api.post<OrderOut>("/orders", payload).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["orders"] });
      qc.invalidateQueries({ queryKey: ["positions"] });
      qc.invalidateQueries({ queryKey: ["account"] });
    },
  });
}

export function useCancelOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.delete<OrderOut>(`/orders/${id}`).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["orders"] });
      qc.invalidateQueries({ queryKey: ["order"] });
      qc.invalidateQueries({ queryKey: ["account"] });
    },
  });
}

export function useDeposit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, amount }: { id: string; amount: string }) =>
      api.post(`/accounts/${id}/deposit`, { amount }).then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
    },
  });
}

export function useResetAccount() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      id,
      starting_balance,
    }: {
      id: string;
      starting_balance?: string;
    }) =>
      api
        .post(`/accounts/${id}/reset`, { starting_balance })
        .then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["account"] });
      qc.invalidateQueries({ queryKey: ["accounts"] });
      qc.invalidateQueries({ queryKey: ["positions"] });
      qc.invalidateQueries({ queryKey: ["orders"] });
    },
  });
}

// ---------------------------------------------------------------------------
// Strategies
// ---------------------------------------------------------------------------

export function useStrategies() {
  return useQuery({
    queryKey: qk.strategies,
    queryFn: () => api.get<StrategyOut[]>("/strategies").then((r) => r.data),
  });
}

export function useStrategy(id: string | null) {
  return useQuery({
    queryKey: id ? qk.strategy(id) : ["strategy", "none"],
    queryFn: () => api.get<StrategyOut>(`/strategies/${id}`).then((r) => r.data),
    enabled: !!id,
  });
}

export function useStrategyTemplates() {
  return useQuery({
    queryKey: qk.templates,
    queryFn: () =>
      api.get<StrategyTemplateOut[]>("/strategy-templates").then((r) => r.data),
  });
}

export function useCreateStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: StrategyCreate) =>
      api.post<StrategyOut>("/strategies", payload).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.strategies }),
  });
}

export function useUpdateStrategy(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: StrategyUpdate) =>
      api.patch<StrategyOut>(`/strategies/${id}`, payload).then((r) => r.data),
    onSuccess: (data) => {
      qc.setQueryData(qk.strategy(id), data);
      qc.invalidateQueries({ queryKey: qk.strategies });
      qc.invalidateQueries({ queryKey: ["strategy-runs"] });
    },
  });
}

export function useDeleteStrategy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api.delete(`/strategies/${id}`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.strategies }),
  });
}

export function useStrategyRuns(strategyId?: string) {
  return useQuery({
    queryKey: qk.runs(strategyId),
    queryFn: () =>
      api
        .get<StrategyRunOut[]>("/strategy-runs", {
          params: strategyId ? { strategy_id: strategyId } : undefined,
        })
        .then((r) => r.data),
  });
}

export function useStrategyRun(id: string | null) {
  return useQuery({
    queryKey: id ? qk.run(id) : ["strategy-run", "none"],
    queryFn: () =>
      api.get<StrategyRunDetailOut>(`/strategy-runs/${id}`).then((r) => r.data),
    enabled: !!id,
    // Poll while a run is still active (backtest/paper in progress).
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ACTIVE_RUN_STATUSES.includes(status) ? 1500 : false;
    },
  });
}

export function useStartBacktest(strategyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: BacktestRequest) =>
      api
        .post<StrategyRunOut>(`/strategies/${strategyId}/backtests`, payload)
        .then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategy-runs"] }),
  });
}

export function useStartPaperRun(strategyId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: PaperRunRequest) =>
      api
        .post<StrategyRunOut>(`/strategies/${strategyId}/runs`, payload)
        .then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategy-runs"] }),
  });
}

export function useStopPaperRun() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      api.post<StrategyRunOut>(`/strategy-runs/${runId}/stop`).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["strategy-runs"] }),
  });
}
