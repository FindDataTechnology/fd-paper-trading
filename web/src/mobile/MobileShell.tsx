import { useState } from "react";
import { Link } from "react-router-dom";
import { useUI } from "../store/ui";
import { useAccountSummary } from "../api/hooks";
import AccountSwitcher from "../components/AccountSwitcher";
import { money } from "../lib/format";
import WatchTab from "./tabs/WatchTab";
import TradeTab from "./tabs/TradeTab";
import PositionsTab from "./tabs/PositionsTab";
import OrdersTab from "./tabs/OrdersTab";
import StrategiesTab from "./tabs/StrategiesTab";

type TabKey = "watch" | "trade" | "positions" | "orders" | "strategies";

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: "watch", label: "Watch", icon: "👁" },
  { key: "trade", label: "Trade", icon: "📈" },
  { key: "positions", label: "Pos", icon: "💼" },
  { key: "orders", label: "Orders", icon: "📋" },
  { key: "strategies", label: "Strat", icon: "⚙" },
];

export default function MobileShell() {
  const [tab, setTab] = useState<TabKey>("watch");
  const activeAccountId = useUI((s) => s.activeAccountId);
  const { data: summary } = useAccountSummary(activeAccountId);

  return (
    <div className="m-shell">
      <header className="m-topbar">
        <span className="m-brand">fd-paper-trading</span>
        <span className="m-account">
          <AccountSwitcher />
        </span>
        <div className="m-equity">
          <div className="label">Equity</div>
          <div className="val">{summary ? money(summary.equity) : "-"}</div>
        </div>
        <Link to="/" className="muted" style={{ fontSize: 11, whiteSpace: "nowrap" }}>
          Desktop
        </Link>
      </header>

      <div className="m-body">
        <nav className="m-tabbar">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={`m-tab${tab === t.key ? " active" : ""}`}
              onClick={() => setTab(t.key)}
            >
              <span className="m-tab-icon">{t.icon}</span>
              <span>{t.label}</span>
            </button>
          ))}
        </nav>

        <main className="m-content">
          {tab === "watch" && <WatchTab onPickSymbol={() => setTab("trade")} />}
          {tab === "trade" && <TradeTab accountId={activeAccountId} />}
          {tab === "positions" && <PositionsTab accountId={activeAccountId} />}
          {tab === "orders" && <OrdersTab accountId={activeAccountId} />}
          {tab === "strategies" && <StrategiesTab accountId={activeAccountId} />}
        </main>
      </div>
    </div>
  );
}
