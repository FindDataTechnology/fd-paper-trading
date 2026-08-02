import { useUI } from "../store/ui";
import OrdersList from "../components/OrdersList";

export default function OrdersPage() {
  const activeAccountId = useUI((s) => s.activeAccountId);
  if (!activeAccountId) {
    return <div className="muted">Select or create an account to begin.</div>;
  }
  return (
    <div className="col">
      <OrdersList accountId={activeAccountId} />
    </div>
  );
}
