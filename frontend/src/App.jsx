import {
  createContext,
  lazy,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { Navigate, Route, Routes, useParams, useSearchParams } from "react-router-dom";

import { api, ApiError } from "./lib/api.js";
import Shell from "./layout/Shell.jsx";
import Login from "./pages/Login.jsx";
import Accounts from "./pages/Accounts.jsx";
import Skeleton from "./components/Skeleton.jsx";
import { useLang, useT } from "./lib/i18n.jsx";

// Split per route so the chart bundle is fetched only once a charting page is
// actually opened -- login and the account picker never pay for it.
const Overview = lazy(() => import("./pages/Overview.jsx"));
const Findings = lazy(() => import("./pages/Findings.jsx"));
const Campaigns = lazy(() => import("./pages/Campaigns.jsx"));
const Breakdowns = lazy(() => import("./pages/Breakdowns.jsx"));
const Portfolio = lazy(() => import("./pages/Portfolio.jsx"));
const Strategist = lazy(() => import("./pages/Strategist.jsx"));
const Creative = lazy(() => import("./pages/Creative.jsx"));
const AdManager = lazy(() => import("./pages/AdManager.jsx"));

const SessionContext = createContext(null);
export const useSession = () => useContext(SessionContext);

const AuditContext = createContext(null);
export const useAudit = () => useContext(AuditContext);

const AccountsContext = createContext({ accounts: [], loading: true });
export const useAccounts = () => useContext(AccountsContext);

/**
 * The account list, fetched once for the whole session.
 *
 * The sidebar switcher needs it on every page; fetching per mount would hit
 * Meta on each navigation for a list that changes about never.
 */
function AccountsProvider({ children }) {
  const [state, setState] = useState({ accounts: [], token: null, loading: true });

  useEffect(() => {
    let cancelled = false;
    api
      .accounts()
      .then((r) => !cancelled && setState({ ...r, loading: false }))
      .catch(() => !cancelled && setState({ accounts: [], token: null, loading: false }));
    return () => {
      cancelled = true;
    };
  }, []);

  return <AccountsContext.Provider value={state}>{children}</AccountsContext.Provider>;
}

/** Reads the reporting window out of the URL, shared by both providers. */
function useWindowParams() {
  const [searchParams, setSearchParams] = useSearchParams();
  const since = searchParams.get("since");
  const until = searchParams.get("until");
  const preset = searchParams.get("preset");

  const window_ = useMemo(
    () => (since && until ? { since, until } : { preset: preset || "last_30d" }),
    [since, until, preset]
  );

  // Writing the window clears the other shape, so a stale `preset` can never
  // sit alongside a `since`/`until` pair in the URL.
  const setWindow = useCallback(
    (next) => {
      const params = new URLSearchParams(searchParams);
      params.delete("preset");
      params.delete("since");
      params.delete("until");
      if (next.preset) params.set("preset", next.preset);
      else {
        params.set("since", next.since);
        params.set("until", next.until);
      }
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams]
  );

  return { window: window_, setWindow, search: searchParams.toString() };
}

/**
 * Loads the audit once per (account, window) and shares it with every tab.
 *
 * A full audit is ~10 Graph calls; refetching it when the user moves from
 * Overview to Findings would make navigation feel broken and burn rate limit
 * for data already in memory.
 */
function AuditProvider({ children }) {
  const { accountId } = useParams();
  const { window: window_, setWindow, search } = useWindowParams();
  // Findings come back written in the interface language, so a switch reloads
  // them (from the server's cache -- no Meta calls).
  const { lang } = useLang();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(
    async ({ fresh = false } = {}) => {
      setLoading(true);
      setError(null);
      try {
        setData(await api.audit(accountId, window_, { fresh }));
      } catch (cause) {
        setError(cause);
        if (!(cause instanceof ApiError && cause.isAuth)) setData(null);
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [accountId, window_, lang]
  );

  useEffect(() => {
    load();
  }, [load]);

  // Refresh means "go past the 5-minute cache", not just "re-render".
  const refresh = useCallback(() => load({ fresh: true }), [load]);

  const value = useMemo(
    () => ({
      accountId,
      accountIds: [accountId],
      mode: "single",
      window: window_,
      setWindow,
      search,
      data,
      loading,
      error,
      reload: refresh,
    }),
    [accountId, window_, setWindow, search, data, loading, error, refresh]
  );

  return <AuditContext.Provider value={value}>{children}</AuditContext.Provider>;
}

/** Same contract as AuditProvider, but for several accounts at once. */
function PortfolioProvider({ children }) {
  const { accountIds: raw } = useParams();
  const { window: window_, setWindow, search } = useWindowParams();
  const ids = useMemo(() => (raw || "").split(",").filter(Boolean), [raw]);
  const { lang } = useLang();

  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(
    async ({ fresh = false } = {}) => {
      setLoading(true);
      setError(null);
      try {
        setData(await api.portfolio(ids, window_, { fresh }));
      } catch (cause) {
        setError(cause);
        if (!(cause instanceof ApiError && cause.isAuth)) setData(null);
      } finally {
        setLoading(false);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [ids, window_, lang]
  );

  useEffect(() => {
    load();
  }, [load]);

  const refresh = useCallback(() => load({ fresh: true }), [load]);

  const value = useMemo(
    () => ({
      accountId: null,
      accountIds: ids,
      mode: "portfolio",
      window: window_,
      setWindow,
      search,
      data,
      loading,
      error,
      reload: refresh,
    }),
    [ids, window_, setWindow, search, data, loading, error, refresh]
  );

  return <AuditContext.Provider value={value}>{children}</AuditContext.Provider>;
}

/** Route chunks fall back to the same skeleton the pages use while loading. */
function Lazy({ children }) {
  return <Suspense fallback={<Skeleton />}>{children}</Suspense>;
}

export default function App() {
  const t = useT();
  const [user, setUser] = useState(undefined); // undefined = still checking

  useEffect(() => {
    api
      .me()
      .then((r) => setUser(r.user))
      .catch(() => setUser(null));
  }, []);

  const session = useMemo(
    () => ({
      user,
      signIn: setUser,
      signOut: async () => {
        await api.logout().catch(() => {});
        setUser(null);
      },
    }),
    [user]
  );

  if (user === undefined) {
    return (
      <div className="login-wrap">
        <div style={{ color: "var(--text-muted)" }}>{t("Loading…")}</div>
      </div>
    );
  }

  if (!user) {
    return (
      <SessionContext.Provider value={session}>
        <Routes>
          <Route path="*" element={<Login />} />
        </Routes>
      </SessionContext.Provider>
    );
  }

  return (
    <SessionContext.Provider value={session}>
      <AccountsProvider>
        <Routes>
          <Route path="/" element={<Accounts />} />

          <Route
            path="/a/:accountId"
            element={
              <AuditProvider>
                <Shell />
              </AuditProvider>
            }
          >
            <Route index element={<Lazy><Overview /></Lazy>} />
            <Route path="findings" element={<Lazy><Findings /></Lazy>} />
            <Route path="campaigns" element={<Lazy><Campaigns /></Lazy>} />
            <Route path="breakdowns" element={<Lazy><Breakdowns /></Lazy>} />
            <Route path="strategist" element={<Lazy><Strategist /></Lazy>} />
            <Route path="creative" element={<Lazy><Creative /></Lazy>} />
            <Route path="manage" element={<Lazy><AdManager /></Lazy>} />
          </Route>

          {/* Several accounts, comma-separated, sharing the same shell. */}
          <Route
            path="/p/:accountIds"
            element={
              <PortfolioProvider>
                <Shell />
              </PortfolioProvider>
            }
          >
            <Route index element={<Lazy><Portfolio /></Lazy>} />
            <Route path="findings" element={<Lazy><Portfolio tab="findings" /></Lazy>} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AccountsProvider>
    </SessionContext.Provider>
  );
}
