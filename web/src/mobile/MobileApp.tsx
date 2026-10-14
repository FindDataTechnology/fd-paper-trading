import { SelectedSymbolProvider } from "../store/selectedSymbol";
import MobileShell from "./MobileShell";

/**
 * Mobile H5 trading surface at `/m`. Shares the same JWT/ProtectedRoute,
 * realtime layer, and React Query hooks as the desktop dashboard, but renders
 * its own mobile shell with a bottom tab bar. Desktop dashboard is untouched.
 */
export default function MobileApp() {
  return (
    <SelectedSymbolProvider>
      <MobileShell />
    </SelectedSymbolProvider>
  );
}
