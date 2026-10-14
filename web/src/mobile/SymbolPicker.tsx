import { useEffect, useState } from "react";
import { useInstruments } from "../api/hooks";
import { useRealtime } from "../realtime/useRealtime";

/** Compact typeahead symbol search; calls onPick(symbol) on select. */
export default function SymbolPicker({ onPick }: { onPick: (symbol: string) => void }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 250);
    return () => window.clearTimeout(t);
  }, [q]);

  const { data: results, isFetching } = useInstruments(debounced);
  const quotes = useRealtime((s) => s.quotes);
  const show = open && debounced.trim().length >= 2;

  function pick(symbol: string) {
    setQ("");
    setDebounced("");
    setOpen(false);
    onPick(symbol);
  }

  return (
    <div className="m-symbolsearch">
      <input
        style={{ width: "100%" }}
        placeholder="Search symbol…"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
      />
      {show && (
        <div className="m-results">
          {isFetching && <div className="muted" style={{ padding: 8 }}>Searching…</div>}
          {!isFetching && results && results.length === 0 && (
            <div className="muted" style={{ padding: 8 }}>
              No instruments found
            </div>
          )}
          {results?.map((r) => {
            const quote = quotes[r.symbol];
            return (
              <button
                key={r.symbol}
                className="m-result"
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(r.symbol);
                }}
              >
                <span>
                  <strong>{r.symbol}</strong>{" "}
                  <span className="muted" style={{ fontSize: 12 }}>{r.name}</span>
                </span>
                {quote && (
                  <span className="muted" style={{ fontSize: 12 }}>
                    {quote.bid}/{quote.ask}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
