import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useInstruments } from "../api/hooks";
import { useRealtime } from "../realtime/useRealtime";

/** Typeahead symbol search with quote preview; navigates to the instrument on select. */
export default function SymbolSearch() {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();
  const blurTimer = useRef<number | null>(null);

  // Debounce: one request per 250ms window.
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
    navigate(`/instruments/${symbol}`);
  }

  return (
    <div style={{ position: "relative" }}>
      <input
        style={{ width: "100%" }}
        placeholder="Search symbol…"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          blurTimer.current = window.setTimeout(() => setOpen(false), 150);
        }}
      />
      {show && (
        <div
          className="card"
          style={{
            position: "absolute",
            top: "100%",
            left: 0,
            right: 0,
            zIndex: 10,
            padding: 0,
            marginTop: 4,
            maxHeight: 320,
            overflow: "auto",
          }}
        >
          {isFetching && <div className="muted" style={{ padding: 8 }}>Searching…</div>}
          {!isFetching && results && results.length === 0 && (
            <div className="muted" style={{ padding: 8 }}>No instruments found</div>
          )}
          {results?.map((r) => {
            const quote = quotes[r.symbol];
            return (
              <button
                key={r.symbol}
                className="secondary"
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  width: "100%",
                  borderRadius: 0,
                  border: "none",
                  borderBottom: "1px solid var(--border)",
                  background: "transparent",
                }}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(r.symbol);
                }}
              >
                <span>
                  <strong>{r.symbol}</strong> <span className="muted">{r.name}</span>
                </span>
                {quote && (
                  <span className="muted" style={{ fontSize: 12 }}>
                    {quote.bid} / {quote.ask}
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
