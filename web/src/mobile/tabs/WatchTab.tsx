import { useEffect } from "react";
import { useRealtime } from "../../realtime/useRealtime";
import { useSelectedSymbol } from "../../store/selectedSymbol";
import { mid, price } from "../../lib/format";
import SymbolPicker from "../SymbolPicker";

const WATCHLIST_SYMBOLS = ["AAPL", "TSLA", "NVDA", "SPY", "QQQ"];

export default function WatchTab({ onPickSymbol }: { onPickSymbol: () => void }) {
  const subscribe = useRealtime((s) => s.subscribe);
  const quotes = useRealtime((s) => s.quotes);
  const { selectedSymbol, setSelectedSymbol } = useSelectedSymbol();

  // Subscribe to all watchlist symbols while this tab is mounted; the
  // ref-counted store cleans up (unsubscribes) on unmount, so realtime
  // bandwidth is scoped to the active tab.
  useEffect(() => {
    const unsubs = WATCHLIST_SYMBOLS.map((s) => subscribe(s));
    return () => unsubs.forEach((u) => u());
  }, [subscribe]);

  function pick(symbol: string) {
    setSelectedSymbol(symbol);
    onPickSymbol();
  }

  return (
    <div className="m-list">
      <SymbolPicker onPick={pick} />
      <div className="m-section-title">Watchlist</div>
      <div className="m-chips">
        {WATCHLIST_SYMBOLS.map((symbol) => {
          const q = quotes[symbol];
          const m = q ? mid(q.bid, q.ask) : null;
          return (
            <button
              key={symbol}
              className={`m-chip${symbol === selectedSymbol ? " active" : ""}`}
              onClick={() => pick(symbol)}
            >
              <strong>{symbol}</strong>
              {m != null && <span className="muted">{price(m)}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
