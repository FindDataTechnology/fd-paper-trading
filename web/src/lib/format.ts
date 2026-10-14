// Formatting and client-side P&L helpers. Pure functions, unit-tested.

export function money(v: string | number, dp = 2): string {
  const n = Number(v);
  if (!isFinite(n)) return "-";
  return n.toLocaleString(undefined, {
    minimumFractionDigits: dp,
    maximumFractionDigits: dp,
  });
}

export function price(v: string | number): string {
  return money(v, 2);
}

export function qty(v: string | number): string {
  const n = Number(v);
  if (!isFinite(n)) return "-";
  return n.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export function mid(bid: string, ask: string): number {
  return (Number(bid) + Number(ask)) / 2;
}

/** Signed unrealized P&L for a position given a live mark price. */
export function unrealizedPnl(qty: string, avgPrice: string, mark: string): number {
  return Number(qty) * (Number(mark) - Number(avgPrice));
}

/** Market value of a position at a live mark price. */
export function positionMarketValue(qty: string, mark: string): number {
  return Number(qty) * Number(mark);
}

export function pnlClass(n: number): string {
  if (n > 0) return "pos";
  if (n < 0) return "neg";
  return "muted";
}

export function signed(n: number, dp = 2): string {
  const s = money(n, dp);
  return n > 0 ? `+${s}` : s;
}

/** Percent formatter for return/win-rate ratios expressed as 0-1 fractions. */
export function pct(v: string | number | null | undefined, dp = 2): string {
  if (v === null || v === undefined) return "-";
  const n = Number(v);
  if (!isFinite(n)) return "-";
  return `${(n * 100).toFixed(dp)}%`;
}

/** CSS class for a run status badge. */
export function runStatusClass(status: string): string {
  switch (status) {
    case "RUNNING":
    case "STARTING":
      return "badge sim";
    case "COMPLETED":
      return "pos";
    case "FAILED":
    case "ERROR":
      return "neg";
    case "STOPPED":
      return "muted";
    default:
      return "muted";
  }
}
