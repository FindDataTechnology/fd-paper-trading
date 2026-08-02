import { useEffect } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useAccounts } from "../api/hooks";
import { useUI } from "../store/ui";

export default function AccountSwitcher() {
  const { data: accounts, isLoading } = useAccounts();
  const activeAccountId = useUI((s) => s.activeAccountId);
  const setActiveAccountId = useUI((s) => s.setActiveAccountId);
  const qc = useQueryClient();

  // Auto-select the first account when none is active.
  useEffect(() => {
    if (!activeAccountId && accounts && accounts.length > 0) {
      setActiveAccountId(accounts[0].id);
    }
  }, [accounts, activeAccountId, setActiveAccountId]);

  const createMutation = useMutation({
    mutationFn: () => api.post("/accounts", { name: "default" }).then((r) => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["accounts"] }),
  });

  if (isLoading) return <span className="muted">loading accounts…</span>;

  if (!accounts || accounts.length === 0) {
    return (
      <button className="secondary" onClick={() => createMutation.mutate()}>
        Create account
      </button>
    );
  }

  return (
    <label className="row gap8" style={{ fontSize: 13 }}>
      <span className="muted">Account</span>
      <select
        value={activeAccountId ?? ""}
        onChange={(e) => setActiveAccountId(e.target.value)}
      >
        {accounts.map((a) => (
          <option key={a.id} value={a.id}>
            {a.name} ({a.venue})
          </option>
        ))}
      </select>
    </label>
  );
}
