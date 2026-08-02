import { useState } from "react";
import { useAccountSummary, useDeposit, usePositions, useResetAccount } from "../api/hooks";
import { useUI } from "../store/ui";
import { useRealtime } from "../realtime/useRealtime";
import { apiErrorMessage } from "../api/client";
import { mid, money } from "../lib/format";
import PositionsTable from "../components/PositionsTable";

export default function Dashboard() {
  const activeAccountId = useUI((s) => s.activeAccountId);
  const { data: summary } = useAccountSummary(activeAccountId);
  const { data: positions } = usePositions(activeAccountId);
  const quotes = useRealtime((s) => s.quotes);
  const deposit = useDeposit();
  const reset = useResetAccount();

  const [amount, setAmount] = useState("");

  // Indicative equity: cash + sum of position market values at live quotes.
  const cash = Number(summary?.cash_balance ?? 0);
  const indicativeEquity =
    cash +
    (positions?.reduce((acc, p) => {
      const q = quotes[p.symbol];
      const mark = q ? mid(q.bid, q.ask) : Number(p.avg_price);
      return acc + Number(p.quantity) * mark;
    }, 0) ?? 0);

  function handleDeposit() {
    if (!activeAccountId || !amount) return;
    if (Number(amount) <= 0) return;
    deposit.mutate({ id: activeAccountId, amount });
    setAmount("");
  }

  function handleReset() {
    if (!activeAccountId) return;
    if (!window.confirm("Reset this account? Cancels open orders and flattens positions.")) return;
    reset.mutate({ id: activeAccountId });
  }

  if (!activeAccountId) {
    return <div className="muted">Select or create an account to begin.</div>;
  }

  return (
    <div className="col">
      <div className="grid" style={{ gridTemplateColumns: "repeat(4, 1fr)" }}>
        <Stat label="Cash" value={money(cash)} />
        <Stat label="Equity (indicative)" value={money(indicativeEquity)} />
        <Stat label="Open positions" value={String(summary?.open_positions ?? 0)} />
        <Stat label="Open orders" value={String(summary?.open_orders ?? 0)} />
      </div>

      <div className="card row gap8">
        <label className="row gap8">
          <span className="muted" style={{ fontSize: 12 }}>Deposit / withdraw</span>
          <input
            style={{ width: 120 }}
            inputMode="decimal"
            placeholder="amount"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
        <button onClick={handleDeposit} disabled={!amount || Number(amount) <= 0}>
          Submit
        </button>
        <button className="secondary danger" onClick={handleReset} disabled={reset.isPending}>
          Reset account
        </button>
        {(deposit.error || reset.error) && (
          <span className="error-text">
            {apiErrorMessage(deposit.error || reset.error)}
          </span>
        )}
      </div>

      <PositionsTable accountId={activeAccountId} />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="card">
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600 }}>{value}</div>
    </div>
  );
}
