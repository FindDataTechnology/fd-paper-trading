import { beforeEach, describe, expect, it, vi } from "vitest";
import { Routes, Route } from "react-router-dom";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import Register from "./Register";
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

function renderRegister() {
  return renderWithProviders(
    <Routes>
      <Route path="/register" element={<Register />} />
      <Route path="/" element={<div>home</div>} />
    </Routes>,
    { route: "/register" }
  );
}

describe("Register", () => {
  it("displays an API error and stays on the page when registration fails", async () => {
    mocks.apiPost.mockRejectedValueOnce({
      response: { status: 409, data: { error: { message: "Email already exists" } } },
    });
    renderRegister();

    fireEvent.change(screen.getByLabelText(/Email/i), { target: { value: "u@e.com" } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: "password" } });
    fireEvent.click(screen.getByRole("button", { name: /Register/i }));

    await waitFor(() =>
      expect(screen.getByText("Email already exists")).toBeInTheDocument()
    );
    expect(screen.queryByText("home")).not.toBeInTheDocument();
    expect(useAuth.getState().token).toBeNull();
  });

  it("registers, auto-logs-in, and navigates on success", async () => {
    mocks.apiPost
      .mockResolvedValueOnce({ data: { id: "u1" } }) // /auth/register
      .mockResolvedValueOnce({ data: { access_token: "tok", expires_in: 3600, user_id: "u1" } }); // /auth/login
    renderRegister();

    fireEvent.change(screen.getByLabelText(/Email/i), { target: { value: "u@e.com" } });
    fireEvent.change(screen.getByLabelText(/Password/i), { target: { value: "password" } });
    fireEvent.click(screen.getByRole("button", { name: /Register/i }));

    await waitFor(() => expect(screen.getByText("home")).toBeInTheDocument());
    expect(useAuth.getState().token).toBe("tok");
  });
});
