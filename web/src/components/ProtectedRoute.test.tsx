import { beforeEach, describe, expect, it } from "vitest";
import { Routes, Route } from "react-router-dom";
import { screen } from "@testing-library/react";
import ProtectedRoute from "./ProtectedRoute";
import { useAuth } from "../store/auth";
import { renderWithProviders } from "../test-utils";

beforeEach(() => useAuth.setState({ token: null }));

describe("ProtectedRoute", () => {
  it("redirects to /login when unauthenticated", () => {
    renderWithProviders(
      <Routes>
        <Route
          path="/secret"
          element={
            <ProtectedRoute>
              <div>secret content</div>
            </ProtectedRoute>
          }
        />
        <Route path="/login" element={<div>login page</div>} />
      </Routes>,
      { route: "/secret" }
    );
    expect(screen.queryByText("secret content")).not.toBeInTheDocument();
    expect(screen.getByText("login page")).toBeInTheDocument();
  });

  it("renders children when authenticated", () => {
    useAuth.setState({ token: "abc" });
    renderWithProviders(
      <Routes>
        <Route
          path="/secret"
          element={
            <ProtectedRoute>
              <div>secret content</div>
            </ProtectedRoute>
          }
        />
      </Routes>,
      { route: "/secret" }
    );
    expect(screen.getByText("secret content")).toBeInTheDocument();
  });
});
