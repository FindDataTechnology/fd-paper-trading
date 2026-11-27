import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../test-utils";
import Layout from "./Layout";

// The nav entry is the whole point of the change: a token surface reachable only
// by typing the URL is not reachable (spec web-shell「令牌面在导航里可达」).
vi.mock("../api/client", () => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn(), patch: vi.fn() },
  apiErrorMessage: () => "Request failed",
}));

vi.mock("../api/hooks", () => ({
  useAccounts: () => ({ data: [], isLoading: false }),
  useInstruments: () => ({ data: [] }),
}));

vi.mock("../realtime/useRealtime", () => ({
  useRealtime: () => undefined,
}));

describe("Layout", () => {
  it("has a nav entry that points at the token surface", () => {
    renderWithProviders(<Layout />);
    const link = screen.getByRole("link", { name: "API tokens" });
    expect(link.getAttribute("href")).toBe("/settings/tokens");
  });
});
