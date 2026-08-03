import { usePositions } from "../api/hooks";
import { useRealtime } from "../realtime/useRealtime";
import { mid, money, pnlClass, signed, unrealizedPnl } from "../lib/format";

export default function DailyPnl({ accountId }: { accountId: string | null }) {
  const { data: positions } = usePositions(accountId);
  const quotes = useRealtime((s) => s.quotes);

  // Calculate daily P&L: realized P&L from today's fills + unrealized P&L from open positions
  const realizedPnl = positions?.reduce((acc, p) => acc + Number(p.realized_pnl), 0) ?? 0;

  const unrealizedPnlTotal =
    positions?.reduce((acc, p) => {
      const q = quotes[p.symbol];
      const mark = q ? mid(q.bid, q.ask) : Number(p.avg_price);
      return acc + unrealizedPnl(p.quantity, p.avg_price, String(mark));
    }, 0) ?? 0;

  const dailyPnl = realizedPnl + unrealizedPnlTotal;

  return (
    <div className="card" style={{ textAlign: "center" }}>
      <div className="muted" style={{ fontSize: 12 }}>Daily P&L (indicative)</div>
      <div className={pnlClass(dailyPnl)} style={{ fontSize: 24, fontWeight: 600 }}>
        {signed(dailyPnl)}
      </div>
      <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>
        Realized: {money(realizedPnl)} | Unrealized: {money(unrealizedPnlTotal)}
      </div>
    </div>
  );
}
