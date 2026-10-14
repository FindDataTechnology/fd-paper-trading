import { create } from "zustand";
import { useEffect } from "react";
import type { OrderOut, Quote } from "../api/types";

export interface StrategyEventPayload {
  run_id: string;
  strategy_id: string;
  type: string;
  message: string;
  data: Record<string, unknown>;
}

type Status = "idle" | "connecting" | "open" | "closed";

// ---- Pure helpers (unit-tested) ----

/** Apply an inbound message to the quote map, returning a new map. */
export function applyQuote(
  quotes: Record<string, Quote>,
  msg: unknown
): Record<string, Quote> {
  const m = msg as { type?: string; symbol?: string; bid?: string; ask?: string; ts?: number };
  if (m?.type === "quote" && m.symbol) {
    return {
      ...quotes,
      [m.symbol]: { symbol: m.symbol, bid: m.bid ?? "0", ask: m.ask ?? "0", ts: m.ts ?? 0 },
    };
  }
  return quotes;
}

export function isOrderEvent(msg: unknown): msg is { type: "order"; order: OrderOut } {
  const m = msg as { type?: string; order?: unknown };
  return m?.type === "order" && !!m?.order;
}

export function isStrategyEvent(
  msg: unknown
): msg is { type: "strategy_event"; event: StrategyEventPayload } {
  const m = msg as { type?: string; event?: unknown };
  return m?.type === "strategy_event" && !!m?.event;
}

/** Ref-counted subscription map delta. Returns a new Map. */
export function nextSubs(
  subs: Map<string, number>,
  symbol: string,
  delta: number
): Map<string, number> {
  const m = new Map(subs);
  const n = (m.get(symbol) ?? 0) + delta;
  if (n <= 0) m.delete(symbol);
  else m.set(symbol, n);
  return m;
}

export function shouldSubscribe(prev: number | undefined, next: number): boolean {
  return (prev ?? 0) === 0 && next > 0;
}

export function shouldUnsubscribe(prev: number | undefined, next: number): boolean {
  return (prev ?? 0) > 0 && next <= 0;
}

function backoffDelay(attempts: number): number {
  return Math.min(1000 * 2 ** attempts, 15_000);
}

// ---- Store ----

interface RealtimeState {
  status: Status;
  quotes: Record<string, Quote>;
  connect: (token: string) => void;
  disconnect: () => void;
  /** Subscribe to a symbol's quotes; returns an unsubscribe function. */
  subscribe: (symbol: string) => () => void;
  /** Register a callback for the user's order/fill events. */
  onOrder: (cb: (o: OrderOut) => void) => () => void;
  /** Register a callback for the user's strategy run events. */
  onStrategyEvent: (cb: (e: StrategyEventPayload) => void) => () => void;
  // internal
  _ws: WebSocket | null;
  _subs: Map<string, number>;
  _orderCbs: Set<(o: OrderOut) => void>;
  _strategyCbs: Set<(e: StrategyEventPayload) => void>;
  _token: string | null;
  _reconnect: number | null;
  _attempts: number;
}

export const useRealtime = create<RealtimeState>((set, get) => {
  function send(msg: object) {
    const ws = get()._ws;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
    }
  }

  function open(token: string) {
    const wsUrl =
      typeof location !== "undefined" && location.protocol === "https:"
        ? "wss:"
        : "ws:";
    const url = `${wsUrl}//${typeof location !== "undefined" ? location.host : "localhost"}/ws`;
    const ws = new WebSocket(url);

    ws.onopen = () => {
      set({ status: "open", _attempts: 0 });
      ws.send(JSON.stringify({ type: "auth", token }));
      // Re-establish subscriptions after (re)connect.
      for (const symbol of get()._subs.keys()) {
        ws.send(JSON.stringify({ type: "subscribe", symbol }));
      }
    };

    ws.onmessage = (ev) => {
      let msg: unknown;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (isOrderEvent(msg)) {
        for (const cb of get()._orderCbs) cb(msg.order);
        return;
      }
      if (isStrategyEvent(msg)) {
        for (const cb of get()._strategyCbs) cb(msg.event);
        return;
      }
      if ((msg as { type?: string })?.type === "quote") {
        set({ quotes: applyQuote(get().quotes, msg) });
      }
    };

    ws.onclose = () => {
      set({ status: "closed", _ws: null });
      const t = get()._token;
      if (t) {
        const attempts = get()._attempts;
        const timer = window.setTimeout(() => {
          set({ _reconnect: null, _attempts: attempts + 1 });
          open(t);
        }, backoffDelay(attempts));
        set({ _reconnect: timer });
      }
    };

    set({ _ws: ws, status: "connecting" });
  }

  return {
    status: "idle",
    quotes: {},
    connect: (token) => {
      // Idempotent: don't reopen if already connected/connecting with a token.
      const { _ws, _token } = get();
      if (_ws && _token === token) return;
      if (_ws) {
        _ws.onclose = null;
        _ws.close();
      }
      set({ _token: token, _attempts: 0 });
      open(token);
    },
    disconnect: () => {
      const { _reconnect, _ws } = get();
      if (_reconnect) window.clearTimeout(_reconnect);
      if (_ws) {
        _ws.onclose = null;
        _ws.close();
      }
      set({
        _ws: null,
        _token: null,
        status: "idle",
        _reconnect: null,
        quotes: {},
      });
    },
    subscribe: (symbol) => {
      const prev = get()._subs.get(symbol);
      const next = nextSubs(get()._subs, symbol, +1);
      set({ _subs: next });
      if (shouldSubscribe(prev, next.get(symbol) ?? 0)) {
        send({ type: "subscribe", symbol });
      }
      return () => {
        const before = get()._subs.get(symbol);
        const after = nextSubs(get()._subs, symbol, -1);
        set({ _subs: after });
        if (shouldUnsubscribe(before, after.get(symbol) ?? 0)) {
          send({ type: "unsubscribe", symbol });
        }
      };
    },
    onOrder: (cb) => {
      get()._orderCbs.add(cb);
      return () => {
        get()._orderCbs.delete(cb);
      };
    },
    onStrategyEvent: (cb) => {
      get()._strategyCbs.add(cb);
      return () => {
        get()._strategyCbs.delete(cb);
      };
    },
    _ws: null,
    _subs: new Map(),
    _orderCbs: new Set(),
    _strategyCbs: new Set(),
    _token: null,
    _reconnect: null,
    _attempts: 0,
  };
});

/** Subscribe to a symbol's live quote; auto-unsubscribes on unmount. */
export function useQuote(symbol: string | null): Quote | undefined {
  const subscribe = useRealtime((s) => s.subscribe);
  const quote = useRealtime((s) => (symbol ? s.quotes[symbol] : undefined));
  useEffect(() => {
    if (!symbol) return;
    return subscribe(symbol);
  }, [symbol, subscribe]);
  return quote;
}

/** Register a callback for the user's order/fill events. */
export function useOrderEvents(cb: (o: OrderOut) => void): void {
  const onOrder = useRealtime((s) => s.onOrder);
  useEffect(() => onOrder(cb), [cb, onOrder]);
}

/** Register a callback for the user's strategy run events (live /ws). */
export function useStrategyEvents(cb: (e: StrategyEventPayload) => void): void {
  const onStrategyEvent = useRealtime((s) => s.onStrategyEvent);
  useEffect(() => onStrategyEvent(cb), [cb, onStrategyEvent]);
}
