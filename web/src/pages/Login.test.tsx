import { beforeEach, describe, expect, it, vi } from "vitest";
import { Routes, Route } from "react-router-dom";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import Login from "./Login";
import { renderWithProviders } from "../test-utils";
import { useAuth } from "../store/auth";

const mocks = vi.hoisted(() => ({ apiPost: vi.fn() }));

vi.mock("../api/client", () => ({
  api: { post: mocks.apiPost, get: vi.fn(), delete: vi.fn() },
  apiErrorMessage: (e: unknown) => {
    const err = e as { response?: { data?: { error?: { message?: string } } } };
    return err?.response?.data?.error?.message ?? "Request failed";
  },
}));

beforeEach(() => {
  mocks.apiPost.mockReset();
  useAuth.setState({ token: null });
});

function renderLogin() {
  return renderWithProviders(
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<div>home</div>} />
    </Routes>,
    { route: "/login" }
  );
}

describe("Login", () => {
  it("displays the API error and stays on the page on invalid credentials", async () => {
    mocks.apiPost.mockRejectedValue({
      response: { status: 401, data: { error: { message: "Invalid credentials" } } },
    });
    renderLogin();

    fireEvent.change(screen.getByLabelText(/Email/i), { target: { value: "u@e.com" } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: "wrong" } });
    fireEvent.click(screen.getByRole("button", { name: /Log in/i }));

    await waitFor(() =>
      expect(screen.getByText("Invalid credentials")).toBeInTheDocument()
    );
    expect(screen.queryByText("home")).not.toBeInTheDocument();
    expect(useAuth.getState().token).toBeNull();
  });

  it("logs in and navigates on success", async () => {
    mocks.apiPost.mockResolvedValue({
      data: { access_token: "tok", expires_in: 3600, user_id: "u1" },
    });
    renderLogin();

    fireEvent.change(screen.getByLabelText(/Email/i), { target: { value: "u@e.com" } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: "password" } });
    fireEvent.click(screen.getByRole("button", { name: /Log in/i }));

    await waitFor(() => expect(screen.getByText("home")).toBeInTheDocument());
    expect(useAuth.getState().token).toBe("tok");
  });
});
