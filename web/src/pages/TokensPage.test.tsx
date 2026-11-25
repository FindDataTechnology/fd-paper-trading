import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import TokensPage from "./TokensPage";
import { renderWithProviders } from "../test-utils";

// The page's whole job is: (1) never render a plaintext from the list, (2) show
// the minted plaintext exactly once, (3) revoke. A regression in (1) is a
// credential leak, so it gets its own assertion.
const mocks = vi.hoisted(() => ({
  api: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
}));

vi.mock("../api/client", () => ({
  api: mocks.api,
  apiErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { error?: { message?: string } } } };
    return err?.response?.data?.error?.message ?? "Request failed";
  },
}));

const row = {
  id: "t1",
  name: "壹座",
  created_at: "2026-11-24T00:00:00Z",
  last_used_at: null,
  revoked_at: null,
  token: null, // list responses never carry the plaintext
};

beforeEach(() => {
  mocks.api.get.mockReset();
  mocks.api.post.mockReset();
  mocks.api.delete.mockReset();
});

describe("TokensPage", () => {
  it("lists tokens without ever rendering a plaintext", async () => {
    mocks.api.get.mockResolvedValue({ data: [row] });
    renderWithProviders(<TokensPage />);
    await waitFor(() => expect(screen.getByTestId("token-table")).toBeTruthy());
    expect(screen.queryByTestId("token-plaintext")).toBeNull();
    expect(screen.getByText("壹座")).toBeTruthy();
  });

  it("shows the minted plaintext exactly once", async () => {
    mocks.api.get.mockResolvedValue({ data: [] });
    mocks.api.post.mockResolvedValue({ data: { ...row, id: "t2", token: "fdt_NEW" } });
    renderWithProviders(<TokensPage />);
    await waitFor(() => expect(screen.getByTestId("token-empty")).toBeTruthy());
    fireEvent.click(screen.getByTestId("token-mint"));
    await waitFor(() =>
      expect(screen.getByTestId("token-plaintext").textContent).toBe("fdt_NEW")
    );
  });

  it("revokes a token and refreshes the list", async () => {
    mocks.api.get.mockResolvedValueOnce({ data: [row] }).mockResolvedValue({ data: [] });
    mocks.api.delete.mockResolvedValue({ status: 204 });
    renderWithProviders(<TokensPage />);
    await waitFor(() => expect(screen.getByTestId("token-revoke")).toBeTruthy());
    fireEvent.click(screen.getByTestId("token-revoke"));
    await waitFor(() => expect(screen.getByTestId("token-empty")).toBeTruthy());
  });
});
