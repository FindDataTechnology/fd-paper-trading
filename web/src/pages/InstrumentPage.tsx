import { useParams } from "react-router-dom";
import { useUI } from "../store/ui";
import PriceChart from "../components/PriceChart";
import OrderTicket from "../components/OrderTicket";

export default function InstrumentPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const activeAccountId = useUI((s) => s.activeAccountId);

  if (!symbol) return <div className="muted">No symbol.</div>;

  return (
    <div className="col">
      <PriceChart symbol={symbol} />
      <OrderTicket key={symbol} symbol={symbol} accountId={activeAccountId} />
    </div>
  );
}
