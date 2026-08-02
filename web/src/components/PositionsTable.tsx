import { usePositions } from "../api/hooks";
import { useQuote } from "../realtime/useRealtime";
import {
  mid,
  money,
  pnlClass,
  positionMarketValue,
  price,
  qty,
  signed,
  unrealizedPnl,
} from "../lib/format";
import type { PositionOut } from "../api/types";

function PositionRow({ pos }: { pos: PositionOut }) {
  const quote = useQuote(pos.symbol);
  const mark = quote ? mid(quote.bid, quote.ask) : Number(pos.avg_price);
  const pnl = unrealizedPnl(pos.quantity, pos.avg_price, String(mark));
  const mv = positionMarketValue(pos.quantity, String(mark));
  return (
    <tr>
      <td>{pos.symbol}</td>
      <td className={Number(pos.quantity) >= 0 ? "pos" : "neg"}>{qty(pos.quantity)}</td>
      <td>{price(pos.avg_price)}</td>
      <td>{quote ? price(mark) : <span className="muted">-</span>}</td>
      <td>{money(mv)}</td>
      <td className={pnlClass(pnl)}>{signed(pnl)}</td>
      <td className={pnlClass(Number(pos.realized_pnl))}>{signed(Number(pos.realized_pnl))}</td>
    </tr>
  );
}

export default function PositionsTable({ accountId }: { accountId: string | null }) {
  const { data: positions, isLoading } = usePositions(accountId);

  return (
    <div className="card col gap8">
      <strong>Positions</strong>
      {isLoading && <div className="muted">Loading positions…</div>}
      {!isLoading && positions && positions.length === 0 && (
        <div className="muted">No open positions.</div>
      )}
      {positions && positions.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Qty</th>
              <th>Avg px</th>
              <th>Mark</th>
              <th>Mkt value</th>
              <th>Unrealized</th>
              <th>Realized</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <PositionRow key={p.id} pos={p} />
            ))}
          </tbody>
        </table>
      )}
      <div className="muted" style={{ fontSize: 12 }}>
        Unrealized P&L and equity are indicative, computed client-side from live
        quotes.
      </div>
    </div>
  );
}
