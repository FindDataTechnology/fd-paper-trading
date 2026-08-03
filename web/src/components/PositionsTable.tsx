import { useState } from "react";
import { usePositions, usePlaceOrder } from "../api/hooks";
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

function PositionRow({ pos, accountId }: { pos: PositionOut; accountId: string | null }) {
  const quote = useQuote(pos.symbol);
  const mark = quote ? mid(quote.bid, quote.ask) : Number(pos.avg_price);
  const pnl = unrealizedPnl(pos.quantity, pos.avg_price, String(mark));
  const mv = positionMarketValue(pos.quantity, String(mark));
  const placeOrder = usePlaceOrder();

  const [showReduce, setShowReduce] = useState(false);
  const [reduceQty, setReduceQty] = useState("");

  const positionQty = Math.abs(Number(pos.quantity));
  const isLong = Number(pos.quantity) > 0;
  const closeSide = isLong ? "SELL" : "BUY";
  const reduceSide = closeSide;

  function handleClose() {
    if (!accountId) return;
    if (!window.confirm(`Close ${pos.symbol} position? Submit market ${closeSide} order for ${positionQty} shares.`)) {
      return;
    }
    placeOrder.mutate({
      account_id: accountId,
      symbol: pos.symbol,
      side: closeSide as "BUY" | "SELL",
      order_type: "MARKET",
      quantity: String(positionQty),
      limit_price: null,
    });
  }

  function handleReduce() {
    if (!accountId) return;
    const qty = Number(reduceQty);
    if (qty <= 0 || qty > positionQty) {
      alert(`Reduce quantity must be between 1 and ${positionQty}`);
      return;
    }
    if (!window.confirm(`Reduce ${pos.symbol} position? Submit market ${reduceSide} order for ${qty} shares.`)) {
      return;
    }
    placeOrder.mutate({
      account_id: accountId,
      symbol: pos.symbol,
      side: reduceSide as "BUY" | "SELL",
      order_type: "MARKET",
      quantity: reduceQty,
      limit_price: null,
    });
    setShowReduce(false);
    setReduceQty("");
  }

  return (
    <tr>
      <td>{pos.symbol}</td>
      <td className={Number(pos.quantity) >= 0 ? "pos" : "neg"}>{qty(pos.quantity)}</td>
      <td>{price(pos.avg_price)}</td>
      <td>{quote ? price(mark) : <span className="muted">-</span>}</td>
      <td>{money(mv)}</td>
      <td className={pnlClass(pnl)}>{signed(pnl)}</td>
      <td className={pnlClass(Number(pos.realized_pnl))}>{signed(Number(pos.realized_pnl))}</td>
      <td>
        <div className="row gap8" style={{ alignItems: "center" }}>
          <button
            className="secondary danger"
            onClick={handleClose}
            disabled={placeOrder.isPending}
            style={{ fontSize: 12, padding: "4px 8px" }}
          >
            Close
          </button>
          {showReduce ? (
            <>
              <input
                type="number"
                placeholder="Qty"
                value={reduceQty}
                onChange={(e) => setReduceQty(e.target.value)}
                style={{ width: 60, fontSize: 12, padding: "4px" }}
                min="1"
                max={positionQty}
              />
              <button
                onClick={handleReduce}
                disabled={placeOrder.isPending || !reduceQty}
                style={{ fontSize: 12, padding: "4px 8px" }}
              >
                Submit
              </button>
              <button
                className="secondary"
                onClick={() => { setShowReduce(false); setReduceQty(""); }}
                style={{ fontSize: 12, padding: "4px 8px" }}
              >
                Cancel
              </button>
            </>
          ) : (
            <button
              className="secondary"
              onClick={() => setShowReduce(true)}
              disabled={placeOrder.isPending}
              style={{ fontSize: 12, padding: "4px 8px" }}
            >
              Reduce
            </button>
          )}
        </div>
      </td>
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
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <PositionRow key={p.id} pos={p} accountId={accountId} />
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
