import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  AccountOut,
  AccountSummary,
  Bar,
  InstrumentSearchResult,
  OrderCreate,
  OrderOut,
  PositionOut,
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
