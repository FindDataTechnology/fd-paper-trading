/**
 * `web-realtime` 规格第二句的闸：断线按退避重连，并在重连后恢复订阅。
 *
 * 原文：The system SHALL maintain a single WebSocket connection in the SPA that
 * authenticates on connect, **auto-reconnects with backoff on drop**, and
 * **re-establishes subscriptions after reconnect**.
 *
 * 2026-11-13 实测：这两条**都没有测试**。既有的 5 条 vitest 全是纯 reducer
 * （applyQuote / isOrderEvent / 订阅计数 / shouldSubscribe），而重连逻辑住在
 * store 的 `open()` 闭包里——那是唯一能测出"断线后会不会自己回来"的地方。
 *
 * 这里用一个假的 WebSocket 把它逼出来：真正被断言的是三件事——
 *   1. onclose 之后**不是立刻重连**，而是等 backoffDelay(attempts) 这么久；
 *   2. 重连成功（onopen）后，之前订阅的每个 symbol **各补发一次 subscribe**；
 *   3. 手动 disconnect 之后**不再重连**（否则用户点了"断开"还会被拉回来）。
 *
 * 第 3 条的真判据是 `_token: null`，不是 `_ws.onclose = null`——我第一版写成后者，
 * 注入对照当场证明它**不红**：`onclose` 里先读 `_token`，手动断开已经把它置空，
 * 所以即便不摘 `onclose` 也不会重连。也就是说 `_ws.onclose = null` 只是一道冗余防御
 * （留着无害），真正的守门人是 `_token`。删掉 `_token: null` 才会红——已验。
 * 这条记下来是因为它正是"两条路给出同一个观测"的典型：先做注入，再认定谁在守门。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { backoffDelay, useRealtime } from "./useRealtime";

class FakeSocket {
  static instances: FakeSocket[] = [];
  static OPEN = 1;

  readyState = 0;
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;

  constructor(public url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = 3;
    this.onclose?.();
  }

  /** 模拟服务端接受连接 */
  accept() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  /** 模拟链路掉线 */
  drop() {
    this.readyState = 3;
    this.onclose?.();
  }
}

const lastSocket = () => FakeSocket.instances[FakeSocket.instances.length - 1];

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", FakeSocket as unknown as typeof WebSocket);
});

afterEach(() => {
  useRealtime.getState().disconnect();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("backoffDelay", () => {
  it("starts at 1s and doubles per attempt", () => {
    expect([0, 1, 2, 3].map(backoffDelay)).toEqual([1000, 2000, 4000, 8000]);
  });

  it("caps at 15s so a long outage never waits minutes", () => {
    // 上限写成 150_000 之类的笔误不会让任何既有测试变红——这条会。
    expect(backoffDelay(4)).toBe(15_000);
    expect(backoffDelay(50)).toBe(15_000);
  });
});

describe("reconnect", () => {
  it("waits for the backoff delay, then reopens with the same token", () => {
    const store = useRealtime.getState();
    store.connect("tok-1");
    lastSocket().accept();
    expect(FakeSocket.instances).toHaveLength(1);

    lastSocket().drop();
    expect(useRealtime.getState().status).toBe("closed");

    // 掉线后**不是**立刻重连
    vi.advanceTimersByTime(999);
    expect(FakeSocket.instances).toHaveLength(1);

    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(2);

    lastSocket().accept();
    expect(JSON.parse(lastSocket().sent[0])).toEqual({ type: "auth", token: "tok-1" });
    expect(useRealtime.getState().status).toBe("open");
  });

  it("re-establishes every subscription after reconnecting", () => {
    const store = useRealtime.getState();
    store.connect("tok-2");
    lastSocket().accept();

    const offA = store.subscribe("AAPL");
    const offB = store.subscribe("MSFT");
    expect(lastSocket().sent.slice(1).map((m) => JSON.parse(m))).toEqual([
      { type: "subscribe", symbol: "AAPL" },
      { type: "subscribe", symbol: "MSFT" },
    ]);

    lastSocket().drop();
    vi.advanceTimersByTime(1000);
    lastSocket().accept();

    const resent = lastSocket().sent.map((m) => JSON.parse(m));
    expect(resent[0]).toEqual({ type: "auth", token: "tok-2" });
    expect(resent.slice(1)).toEqual([
      { type: "subscribe", symbol: "AAPL" },
      { type: "subscribe", symbol: "MSFT" },
    ]);

    offA();
    offB();
  });

  it("does not reconnect after a manual disconnect", () => {
    const store = useRealtime.getState();
    store.connect("tok-3");
    lastSocket().accept();

    useRealtime.getState().disconnect();
    // 手动断开把 onclose 摘掉了：再推进时间也不该冒出第二个 socket
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(useRealtime.getState().status).toBe("idle");
    expect(useRealtime.getState()._token).toBeNull();
  });
});
