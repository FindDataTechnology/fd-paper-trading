import { create } from "zustand";

interface UIState {
  activeAccountId: string | null;
  setActiveAccountId: (id: string | null) => void;
}

// Client-only UI state: the currently selected account. Not persisted.
export const useUI = create<UIState>((set) => ({
  activeAccountId: null,
  setActiveAccountId: (id) => set({ activeAccountId: id }),
}));
