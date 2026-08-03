import { createContext, useContext, useState, type ReactNode } from "react";

interface SelectedSymbolContextType {
  selectedSymbol: string;
  setSelectedSymbol: (symbol: string) => void;
}

const SelectedSymbolContext = createContext<SelectedSymbolContextType | undefined>(undefined);

export function SelectedSymbolProvider({ children }: { children: ReactNode }) {
  const [selectedSymbol, setSelectedSymbol] = useState<string>("AAPL");

  return (
    <SelectedSymbolContext.Provider value={{ selectedSymbol, setSelectedSymbol }}>
      {children}
    </SelectedSymbolContext.Provider>
  );
}

export function useSelectedSymbol() {
  const context = useContext(SelectedSymbolContext);
  if (!context) {
    throw new Error("useSelectedSymbol must be used within SelectedSymbolProvider");
  }
  return context;
}
