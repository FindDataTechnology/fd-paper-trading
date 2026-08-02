import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";

/** A QueryClient with retries/gc disabled so tests fail fast and deterministically. */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0, staleTime: 0 },
      mutations: { retry: false },
    },
  });
}

/** Render a component wrapped in a fresh QueryClient + MemoryRouter. */
export function renderWithProviders(
  ui: ReactElement,
  opts: { route?: string; queryClient?: QueryClient } = {}
) {
  const qc = opts.queryClient ?? makeQueryClient();
  const result = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[opts.route ?? "/"]}>{ui}</MemoryRouter>
    </QueryClientProvider>
  );
  return { ...result, queryClient: qc };
}
