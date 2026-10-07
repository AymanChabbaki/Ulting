import { currentLang } from "./i18n.jsx";

/**
 * API client.
 *
 * Every call is same-origin (Vite proxies /api to the FastAPI process) so the
 * HttpOnly session cookie rides along. A 401 anywhere means the session lapsed;
 * callers surface that as a redirect to login rather than an error toast.
 */

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
  get isAuth() {
    return this.status === 401;
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    const detail = body?.detail;
    const message =
      typeof detail === "string"
        ? detail
        : detail?.message || `Request failed (${response.status})`;
    throw new ApiError(message, response.status, detail);
  }
  return body;
}

/** Serialise a window as query params -- preset OR since/until, never both. */
export function windowQuery(window = {}) {
  const params = new URLSearchParams();
  if (window.since && window.until) {
    params.set("since", window.since);
    params.set("until", window.until);
  } else {
    params.set("preset", window.preset || "last_30d");
  }
  return params.toString();
}

// Findings are written on the server, so the interface language travels with
// every request that returns them.
const langParam = () => `&lang=${currentLang()}`;

export const api = {
  login: (username, password) =>
    request("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => request("/api/auth/logout", { method: "POST" }),
  me: () => request("/api/auth/me"),
  health: () => request("/api/health"),
  accounts: () => request("/api/accounts"),
  audit: (accountId, window, { fresh = false } = {}) =>
    request(`/api/audit/${accountId}?${windowQuery(window)}${fresh ? "&fresh=1" : ""}${langParam()}`),
  portfolio: (accountIds, window, { fresh = false } = {}) =>
    request(
      `/api/portfolio?accounts=${accountIds.join(",")}&${windowQuery(window)}${
        fresh ? "&fresh=1" : ""
      }${langParam()}`
    ),
  breakdown: (accountId, cut, window, { fresh = false } = {}) =>
    request(
      `/api/breakdown/${accountId}?cut=${cut}&${windowQuery(window)}${fresh ? "&fresh=1" : ""}`
    ),
  exportUrl: (accountId, window) => `/api/export/${accountId}.csv?${windowQuery(window)}${langParam()}`,
  reportUrl: (accountId, window, lang = "fr") =>
    `/api/report/${accountId}.pdf?${windowQuery(window)}&lang=${lang}`,
};
