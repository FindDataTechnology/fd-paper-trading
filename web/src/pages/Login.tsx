import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, apiErrorMessage } from "../api/client";
import { useAuth } from "../store/auth";
import type { TokenResponse } from "../api/types";

export default function Login() {
  const setToken = useAuth((s) => s.setToken);
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<TokenResponse>("/auth/login", { email, password });
      setToken(r.data.access_token);
      navigate(from, { replace: true });
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ maxWidth: 360, margin: "80px auto" }}>
      <form className="card col gap8" onSubmit={submit}>
        <h2 style={{ margin: 0 }}>Log in</h2>
        <label className="col">
          <span className="muted" style={{ fontSize: 12 }}>Email</span>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="col">
          <span className="muted" style={{ fontSize: 12 }}>Password</span>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="error-text">{error}</div>}
        <button type="submit" disabled={busy}>
          {busy ? "Logging in…" : "Log in"}
        </button>
        <div className="muted" style={{ fontSize: 13 }}>
          No account? <Link to="/register">Register</Link>
        </div>
      </form>
    </div>
  );
}
