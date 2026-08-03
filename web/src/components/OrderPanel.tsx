import OrderTicket from "./OrderTicket";
import { useSelectedSymbol } from "../store/selectedSymbol";
import { useUI } from "../store/ui";

export default function OrderPanel() {
  const { selectedSymbol } = useSelectedSymbol();
  const activeAccountId = useUI((s) => s.activeAccountId);

  return (
    <div className="card col gap8">
      <OrderTicket symbol={selectedSymbol} accountId={activeAccountId} />
    </div>
  );
}
