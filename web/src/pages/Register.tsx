import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, apiErrorMessage } from "../api/client";
import { useAuth } from "../store/auth";
import type { TokenResponse } from "../api/types";

export default function Register() {
  const setToken = useAuth((s) => s.setToken);
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.post("/auth/register", { email, password });
      // Auto-login after registration.
      const r = await api.post<TokenResponse>("/auth/login", { email, password });
      setToken(r.data.access_token);
      navigate("/", { replace: true });
    } catch (err) {
      setError(apiErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ maxWidth: 360, margin: "80px auto" }}>
      <form className="card col gap8" onSubmit={submit}>
        <h2 style={{ margin: 0 }}>Register</h2>
        <label className="col">
          <span className="muted" style={{ fontSize: 12 }}>Email</span>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="col">
          <span className="muted" style={{ fontSize: 12 }}>Password</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
          />
        </label>
        {error && <div className="error-text">{error}</div>}
        <button type="submit" disabled={busy}>
          {busy ? "Registering…" : "Register"}
        </button>
        <div className="muted" style={{ fontSize: 13 }}>
          Have an account? <Link to="/login">Log in</Link>
        </div>
      </form>
    </div>
  );
}
