import { useState } from "react";
import { useAccountSummary, useDeposit, usePositions, useResetAccount } from "../api/hooks";
import { useUI } from "../store/ui";
import { useRealtime } from "../realtime/useRealtime";
import { apiErrorMessage } from "../api/client";
import { mid, money } from "../lib/format";
import PositionsTable from "../components/PositionsTable";
import Watchlist from "../components/Watchlist";
import OrderPanel from "../components/OrderPanel";
import PriceChart from "../components/PriceChart";
import DailyPnl from "../components/DailyPnl";
import { SelectedSymbolProvider, useSelectedSymbol } from "../store/selectedSymbol";

export default function Dashboard() {
  const activeAccountId = useUI((s) => s.activeAccountId);

  if (!activeAccountId) {
    return <div className="muted">Select or create an account to begin.</div>;
  }

  return (
    <SelectedSymbolProvider>
      <DashboardContent accountId={activeAccountId} />
    </SelectedSymbolProvider>
  );
}

function DashboardContent({ accountId }: { accountId: string }) {
  const { data: summary } = useAccountSummary(accountId);
  const { data: positions } = usePositions(accountId);
  const quotes = useRealtime((s) => s.quotes);
  const deposit = useDeposit();
  const reset = useResetAccount();
  const { selectedSymbol } = useSelectedSymbol();

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
    if (!accountId || !amount) return;
    if (Number(amount) <= 0) return;
    deposit.mutate({ id: accountId, amount });
    setAmount("");
  }

  function handleReset() {
    if (!accountId) return;
    if (!window.confirm("Reset this account? Cancels open orders and flattens positions.")) return;
    reset.mutate({ id: accountId });
  }

  return (
    <div className="col">
      <div className="grid" style={{ gridTemplateColumns: "repeat(5, 1fr)" }}>
        <Stat label="Cash" value={money(cash)} />
        <Stat label="Equity (indicative)" value={money(indicativeEquity)} />
        <DailyPnl accountId={accountId} />
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

      {/* Three-column trading workspace */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "250px 1fr 350px",
          gap: 16,
          minHeight: 500,
        }}
      >
        <Watchlist />
        <PriceChart symbol={selectedSymbol} />
        <OrderPanel />
      </div>

      <PositionsTable accountId={accountId} />
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
