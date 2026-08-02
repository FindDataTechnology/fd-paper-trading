import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface AuthState {
  token: string | null;
  setToken: (token: string | null) => void;
  logout: () => void;
}

// JWT kept in sessionStorage (cleared when the tab closes). Short-lived access
// tokens; refresh-token rotation is deferred (re-login on expiry).
export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      setToken: (token) => set({ token }),
      logout: () => set({ token: null }),
    }),
    {
      name: "fd-trade-auth",
      storage: createJSONStorage(() => sessionStorage),
    }
  )
);
