import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../store/auth";
import AccountSwitcher from "./AccountSwitcher";
import SymbolSearch from "./SymbolSearch";

export default function Layout() {
  const logout = useAuth((s) => s.logout);
  const navigate = useNavigate();

  function handleLogout() {
    logout();
    navigate("/login", { replace: true });
  }

  return (
    <div style={{ minHeight: "100vh" }}>
      <header
        style={{
          display: "flex",
          gap: 16,
          alignItems: "center",
          padding: "10px 16px",
          borderBottom: "1px solid var(--border)",
          background: "var(--panel)",
          flexWrap: "wrap",
        }}
      >
        <strong style={{ fontSize: 16 }}>fd-trade</strong>
        <nav style={{ display: "flex", gap: 12 }}>
          <NavLink to="/" end>
            Dashboard
          </NavLink>
          <NavLink to="/orders">Orders</NavLink>
        </nav>
        <div style={{ flex: 1, minWidth: 220, maxWidth: 360 }}>
          <SymbolSearch />
        </div>
        <AccountSwitcher />
        <button className="secondary" onClick={handleLogout}>
          Log out
        </button>
      </header>
      <main style={{ padding: 16, maxWidth: 1200, margin: "0 auto" }}>
        <Outlet />
      </main>
    </div>
  );
}
