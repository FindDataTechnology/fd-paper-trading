import { useEffect } from "react";
import { useRealtime } from "../realtime/useRealtime";
import { useSelectedSymbol } from "../store/selectedSymbol";
import { mid, price } from "../lib/format";

const WATCHLIST_SYMBOLS = ["AAPL", "TSLA", "NVDA", "SPY", "QQQ"];

export default function Watchlist() {
  const subscribe = useRealtime((s) => s.subscribe);
  const quotes = useRealtime((s) => s.quotes);
  const { selectedSymbol, setSelectedSymbol } = useSelectedSymbol();

  // Subscribe to all watchlist symbols on mount, unsubscribe on unmount
  useEffect(() => {
    const unsubscribes = WATCHLIST_SYMBOLS.map((symbol) => subscribe(symbol));
    return () => {
      unsubscribes.forEach((unsub) => unsub());
    };
  }, [subscribe]);

  return (
    <div className="card col gap8">
      <strong>Watchlist</strong>
      <table>
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Bid</th>
            <th>Ask</th>
            <th>Mid</th>
          </tr>
        </thead>
        <tbody>
          {WATCHLIST_SYMBOLS.map((symbol) => {
            const quote = quotes[symbol];
            const isSelected = symbol === selectedSymbol;
            return (
              <tr
                key={symbol}
                onClick={() => setSelectedSymbol(symbol)}
                style={{
                  cursor: "pointer",
                  background: isSelected ? "var(--accent-light, #e3f2fd)" : "transparent",
                }}
              >
                <td>
                  <strong>{symbol}</strong>
                </td>
                <td>{quote ? price(quote.bid) : <span className="muted">-</span>}</td>
                <td>{quote ? price(quote.ask) : <span className="muted">-</span>}</td>
                <td>{quote ? price(String(mid(quote.bid, quote.ask))) : <span className="muted">-</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
