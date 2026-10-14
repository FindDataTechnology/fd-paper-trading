import { useState } from "react";
import { usePositions, usePlaceOrder } from "../../api/hooks";
import { useQuote } from "../../realtime/useRealtime";
import {
  mid,
  pnlClass,
  price,
  qty,
  signed,
  unrealizedPnl,
} from "../../lib/format";
import type { PositionOut } from "../../api/types";

function PositionCard({
  pos,
  accountId,
}: {
  pos: PositionOut;
  accountId: string | null;
}) {
  const quote = useQuote(pos.symbol);
  const mark = quote ? mid(quote.bid, quote.ask) : Number(pos.avg_price);
  const pnl = unrealizedPnl(pos.quantity, pos.avg_price, String(mark));
  const placeOrder = usePlaceOrder();
  const [reducing, setReducing] = useState(false);
  const [reduceQty, setReduceQty] = useState("");

  const positionQty = Math.abs(Number(pos.quantity));
  const isLong = Number(pos.quantity) > 0;
  const closeSide = isLong ? "SELL" : "BUY";

  function close() {
    if (!accountId) return;
    if (
      !window.confirm(
        `Close ${pos.symbol}? Market ${closeSide} ${positionQty} shares.`
      )
    )
      return;
    placeOrder.mutate({
      account_id: accountId,
      symbol: pos.symbol,
      side: closeSide as "BUY" | "SELL",
      order_type: "MARKET",
      quantity: String(positionQty),
      limit_price: null,
    });
  }

  function reduce() {
    if (!accountId) return;
    const n = Number(reduceQty);
    if (n <= 0 || n > positionQty) {
      alert(`Quantity must be between 1 and ${positionQty}`);
      return;
    }
    if (
      !window.confirm(
        `Reduce ${pos.symbol}? Market ${closeSide} ${n} shares.`
      )
    )
      return;
    placeOrder.mutate({
      account_id: accountId,
      symbol: pos.symbol,
      side: closeSide as "BUY" | "SELL",
      order_type: "MARKET",
      quantity: reduceQty,
      limit_price: null,
    });
    setReducing(false);
    setReduceQty("");
  }

  return (
    <div className="m-card">
      <div className="m-between">
        <strong>{pos.symbol}</strong>
        <span className={pnlClass(pnl)} style={{ fontWeight: 600 }}>
          {signed(pnl)}
        </span>
      </div>
      <div className="m-row" style={{ fontSize: 13, marginTop: 6 }}>
        <span className={isLong ? "pos" : "neg"}>{qty(pos.quantity)}</span>
        <span className="muted">@ {price(pos.avg_price)}</span>
        <span className="muted">mark {quote ? price(mark) : "-"}</span>
        <span className="m-grow" />
        <span className="muted" style={{ fontSize: 12 }}>
          realized{" "}
          <span className={pnlClass(Number(pos.realized_pnl))}>
            {signed(Number(pos.realized_pnl))}
          </span>
        </span>
      </div>

      {reducing ? (
        <div className="m-row" style={{ marginTop: 8 }}>
          <input
            style={{ width: 80 }}
            type="number"
            inputMode="decimal"
            placeholder="Qty"
            value={reduceQty}
            onChange={(e) => setReduceQty(e.target.value)}
            min={1}
            max={positionQty}
          />
          <button
            className="m-btn-sm"
            onClick={reduce}
            disabled={placeOrder.isPending || !reduceQty}
          >
            Submit
          </button>
          <button
            className="secondary m-btn-sm"
            onClick={() => {
              setReducing(false);
              setReduceQty("");
            }}
          >
            Cancel
          </button>
        </div>
      ) : (
        <div className="m-row" style={{ marginTop: 8 }}>
          <button
            className="secondary danger m-btn-sm"
            onClick={close}
            disabled={placeOrder.isPending}
          >
            Close
          </button>
          <button
            className="secondary m-btn-sm"
            onClick={() => setReducing(true)}
            disabled={placeOrder.isPending}
          >
            Reduce
          </button>
        </div>
      )}
    </div>
  );
}

export default function PositionsTab({
  accountId,
}: {
  accountId: string | null;
}) {
  const { data: positions, isLoading } = usePositions(accountId);

  if (isLoading) return <div className="m-empty">Loading positions…</div>;
  if (!positions || positions.length === 0)
    return <div className="m-empty">No open positions.</div>;

  return (
    <div className="m-list">
      <div className="m-section-title">Positions</div>
      {positions.map((p) => (
        <PositionCard key={p.id} pos={p} accountId={accountId} />
      ))}
    </div>
  );
}
