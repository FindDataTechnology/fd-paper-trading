import { describe, expect, it } from "vitest";
import {
  mid,
  money,
  pnlClass,
  positionMarketValue,
  signed,
  unrealizedPnl,
} from "./format";

describe("format helpers", () => {
  it("formats money with 2dp", () => {
    expect(money("1234.5")).toBe("1,234.50");
    expect(money(0)).toBe("0.00");
    expect(money("nan-ish")).toBe("-");
  });

  it("computes mid", () => {
    expect(mid("100.00", "100.10")).toBeCloseTo(100.05);
  });

  it("computes unrealized P&L for a long", () => {
    expect(unrealizedPnl("10", "100", "110")).toBeCloseTo(100);
    expect(unrealizedPnl("10", "100", "95")).toBeCloseTo(-50);
  });

  it("computes position market value", () => {
    expect(positionMarketValue("10", "110")).toBeCloseTo(1100);
  });

  it("classifies pnl", () => {
    expect(pnlClass(1)).toBe("pos");
    expect(pnlClass(-1)).toBe("neg");
    expect(pnlClass(0)).toBe("muted");
  });

  it("signs numbers", () => {
    expect(signed(50)).toBe("+50.00");
    expect(signed(-50)).toBe("-50.00");
  });
});
