import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../store/auth";

/** Redirect unauthenticated users to /login, preserving the destination. */
export default function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const token = useAuth((s) => s.token);
  const location = useLocation();
  if (!token) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}
