import { useEffect } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { useAuth } from "./store/auth";
import { useUI } from "./store/ui";
import { useRealtime } from "./realtime/useRealtime";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import OrdersPage from "./pages/OrdersPage";
import OrderDetailPage from "./pages/OrderDetailPage";
import InstrumentPage from "./pages/InstrumentPage";
import ChatProvider from "./components/ChatProvider";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

/** Open the realtime WebSocket when authenticated; close it on logout. */
function RealtimeBridge() {
  const token = useAuth((s) => s.token);
  const connect = useRealtime((s) => s.connect);
  const disconnect = useRealtime((s) => s.disconnect);
  useEffect(() => {
    if (token) connect(token);
    else disconnect();
  }, [token, connect, disconnect]);
  return null;
}

function AppContent() {
  const activeAccountId = useUI((s) => s.activeAccountId);

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        element={
          <ProtectedRoute>
            <ChatProvider activeAccountId={activeAccountId || undefined}>
              <Layout />
            </ChatProvider>
          </ProtectedRoute>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="orders" element={<OrdersPage />} />
        <Route path="orders/:orderId" element={<OrderDetailPage />} />
        <Route path="instruments/:symbol" element={<InstrumentPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <RealtimeBridge />
        <AppContent />
      </BrowserRouter>
    </QueryClientProvider>
  );
}
