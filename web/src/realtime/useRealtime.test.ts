import { describe, expect, it } from "vitest";
import {
  applyQuote,
  isOrderEvent,
  nextSubs,
  shouldSubscribe,
  shouldUnsubscribe,
} from "./useRealtime";
import type { OrderOut } from "../api/types";

const order: OrderOut = {
  id: "o1",
  account_id: "a1",
  client_order_id: null,
  instrument_id: "AAPL.SIM",
  symbol: "AAPL",
  side: "BUY",
  order_type: "MARKET",
  quantity: "10",
  limit_price: null,
  status: "FILLED",
  filled_quantity: "10",
  avg_fill_price: "150.00",
  reject_reason: null,
  created_at: "",
  updated_at: "",
  fills: [],
};

describe("applyQuote", () => {
  it("stores a quote by symbol", () => {
    const q = applyQuote({}, { type: "quote", symbol: "AAPL", bid: "150", ask: "150.1", ts: 1 });
    expect(q.AAPL).toEqual({ symbol: "AAPL", bid: "150", ask: "150.1", ts: 1 });
  });

  it("ignores non-quote messages", () => {
    expect(applyQuote({ X: {} as never }, { type: "order", order: order })).toEqual({ X: {} });
  });
});

describe("isOrderEvent", () => {
  it("detects order events", () => {
    expect(isOrderEvent({ type: "order", order })).toBe(true);
    expect(isOrderEvent({ type: "quote" })).toBe(false);
    expect(isOrderEvent({})).toBe(false);
  });
});

describe("subscription ref-counting", () => {
  it("increments and decrements, removing at zero", () => {
    let subs = new Map<string, number>();
    subs = nextSubs(subs, "AAPL", +1);
    expect(subs.get("AAPL")).toBe(1);
    subs = nextSubs(subs, "AAPL", +1);
    expect(subs.get("AAPL")).toBe(2);
    subs = nextSubs(subs, "AAPL", -1);
    expect(subs.get("AAPL")).toBe(1);
    subs = nextSubs(subs, "AAPL", -1);
    expect(subs.has("AAPL")).toBe(false);
  });

  it("flags subscribe/unsubscribe transitions", () => {
    expect(shouldSubscribe(undefined, 1)).toBe(true);
    expect(shouldSubscribe(1, 2)).toBe(false);
    expect(shouldUnsubscribe(1, 0)).toBe(true);
    expect(shouldUnsubscribe(2, 1)).toBe(false);
  });
});
