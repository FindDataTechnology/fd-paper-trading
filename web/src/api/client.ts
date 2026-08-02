import axios from "axios";
import { useAuth } from "../store/auth";

export const api = axios.create({ baseURL: "/api/v1" });

// Attach the JWT to every request.
api.interceptors.request.use((config) => {
  const token = useAuth.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// On 401, clear the session and force re-login.
api.interceptors.response.use(
  (r) => r,
  (error) => {
    if (error?.response?.status === 401) {
      useAuth.getState().logout();
      if (typeof location !== "undefined" && location.pathname !== "/login") {
        location.assign("/login");
      }
    }
    return Promise.reject(error);
  }
);

/** Extract a human-readable message from an API error response. */
export function apiErrorMessage(error: unknown): string {
  const body = (error as { response?: { data?: { error?: { message?: string } } } })
    ?.response?.data?.error;
  if (body?.message) return body.message;
  return (error as { message?: string })?.message ?? "Request failed";
}
