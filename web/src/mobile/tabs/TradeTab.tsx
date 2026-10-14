import { useState } from "react";
import PriceChart from "../../components/PriceChart";
import OrderTicket from "../../components/OrderTicket";
import { useSelectedSymbol } from "../../store/selectedSymbol";
import SymbolPicker from "../SymbolPicker";

export default function TradeTab({ accountId }: { accountId: string | null }) {
  const { selectedSymbol, setSelectedSymbol } = useSelectedSymbol();
  const [picking, setPicking] = useState(false);

  return (
    <div className="m-list">
      <div className="m-card m-between">
        <button className="secondary m-btn-sm" onClick={() => setPicking((v) => !v)}>
          {picking ? "Cancel" : "Change"}
        </button>
        <strong style={{ fontSize: 18 }}>{selectedSymbol}</strong>
        <span className="badge sim">sim</span>
      </div>

      {picking && (
        <SymbolPicker
          onPick={(s) => {
            setSelectedSymbol(s);
            setPicking(false);
          }}
        />
      )}

      <PriceChart symbol={selectedSymbol} height={260} />

      {/* key by symbol so the ticket form resyncs when the symbol changes */}
      <OrderTicket key={selectedSymbol} symbol={selectedSymbol} accountId={accountId} />
    </div>
  );
}
