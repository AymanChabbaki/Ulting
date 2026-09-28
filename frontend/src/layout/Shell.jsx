import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import * as Tooltip from "@radix-ui/react-tooltip";
import { AnimatePresence, motion } from "motion/react";
import { Toaster, toast } from "sonner";
import {
  ChevronLeft,
  Download,
  Gauge,
  Layers,
  LogOut,
  RefreshCw,
  Palette,
  SlidersHorizontal,
  Sparkles,
  Table2,
  TriangleAlert,
  Info,
} from "lucide-react";

import { useAudit, useSession } from "../App.jsx";
import { api } from "../lib/api.js";
import { money } from "../lib/format.js";
import DateRangePicker from "../components/DateRangePicker.jsx";
import AccountSwitcher from "../components/AccountSwitcher.jsx";

const NAV_SINGLE = [
  { to: "", label: "Overview", end: true, Icon: Gauge },
  { to: "findings", label: "Audit findings", Icon: TriangleAlert, badge: "findings" },
  { to: "campaigns", label: "Campaigns", Icon: Table2, badge: "campaigns" },
  { to: "breakdowns", label: "Breakdowns", Icon: Layers },
  { to: "manage", label: "Ad manager", Icon: SlidersHorizontal },
  { to: "strategist", label: "Strategist", Icon: Sparkles, accent: true },
  { to: "creative", label: "Creative studio", Icon: Palette, accent: true },
];

// Campaigns and breakdowns are per-account questions; across a portfolio they
// would mean stitching together entities that never competed in one auction.
const NAV_PORTFOLIO = [
  { to: "", label: "Comparison", end: true, Icon: Layers },
  { to: "findings", label: "All findings", Icon: TriangleAlert, badge: "findings" },
];

const COLLAPSE_KEY = "ulting.sidebar.collapsed";

/** Wraps a rail item so its label is still reachable when collapsed. */
function RailTip({ label, collapsed, children }) {
  if (!collapsed) return children;
  return (
    <Tooltip.Root delayDuration={120}>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className="tip" side="right" sideOffset={10}>
          {label}
          <Tooltip.Arrow className="tip-arrow" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export default function Shell() {
  const { data, loading, error, window: win, setWindow, search, accountId, accountIds, mode, reload } =
    useAudit();
  const { user, signOut } = useSession();
  const navigate = useNavigate();
  const location = useLocation();

  // Read inside the initialiser, not an effect: restoring it after mount makes
  // the sidebar visibly snap shut on every page load.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    } catch {
      /* private mode: the layout still works, it just will not persist */
    }
  }, [collapsed]);

  // Surface load failures as a toast as well as the inline box -- a rate-limit
  // 429 arriving while you are reading a table is otherwise easy to miss.
  useEffect(() => {
    if (!error) return;
    toast.error("Could not load this account", { description: error.message });
  }, [error]);

  const isPortfolio = mode === "portfolio";
  const NAV = isPortfolio ? NAV_PORTFOLIO : NAV_SINGLE;

  const account = data?.account;
  const counts = isPortfolio ? data?.counts : data?.audit?.counts;
  const badges = {
    findings: isPortfolio ? data?.findings?.length : data?.audit?.findings?.length,
    campaigns: data?.campaigns?.length,
  };
  const urgent = (counts?.critical || 0) + (counts?.high || 0);
  const warnings = data?.warnings || [];

  // The sidebar mini-stats have to stay currency-honest: a portfolio spanning
  // USD and AED has no single spend figure to show.
  const combined = data?.combined;
  const miniSpend = isPortfolio
    ? combined?.mixedCurrency
      ? Object.entries(combined.spendByCurrency || {})
          .map(([c, v]) => money(v, c, { compact: true }))
          .join(" + ")
      : money(combined?.spend || 0, combined?.currency || "USD")
    : money(data?.summary?.metrics?.spend || 0, account?.currency || "USD");
  const miniResults = isPortfolio
    ? Math.round(combined?.results || 0)
    : Math.round(data?.summary?.metrics?.results || 0);
  const miniWaste = isPortfolio
    ? Object.entries(data?.estimatedWasteByCurrency || {})
        .map(([c, v]) => money(v, c, { compact: true }))
        .join(" + ")
    : money(data?.summary?.estimatedWaste || 0, account?.currency || "USD");
  const miniResultLabel = isPortfolio
    ? "Results"
    : data?.summary?.metrics?.resultLabel || "Results";

  function handleRefresh() {
    toast.promise(Promise.resolve(reload()), {
      loading: "Refreshing from Meta…",
      success: "Data refreshed",
      error: "Refresh failed",
    });
  }

  return (
    <Tooltip.Provider>
      <div className={`shell ${collapsed ? "is-collapsed" : ""}`}>
        <Toaster position="bottom-right" richColors closeButton />

        <aside className="sidebar">
          <div className="sidebar-brand">
            <a
              className="brand"
              href="/"
              onClick={(e) => {
                e.preventDefault();
                navigate("/");
              }}
            >
              <img className="brand-mark" src="/ulting-mark.png" alt="" />
              <img className="brand-lockup" src="/ulting-wordmark-dark.png" alt="Ulting" />
            </a>
            <button
              className="sidebar-toggle"
              onClick={() => setCollapsed((v) => !v)}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            >
              <ChevronLeft size={16} />
            </button>
          </div>

          <div className="sidebar-scroll">
            <div className="nav-group">
              <div className="nav-label">Account</div>
              <AccountSwitcher activeIds={accountIds} search={search} />
            </div>

            <nav className="nav-group">
              <div className="nav-label">Analysis</div>
              {NAV.map(({ to, label, end, Icon, badge, accent }) => (
                <RailTip key={label} label={label} collapsed={collapsed}>
                  <NavLink
                    // Carrying the search string is what keeps the chosen
                    // window alive across pages: a bare `to` drops it and the
                    // provider falls back to the default.
                    to={{ pathname: to, search }}
                    end={end}
                    className={({ isActive }) =>
                      `nav-item ${isActive ? "active" : ""} ${accent ? "is-accent" : ""}`
                    }
                  >
                    <span className="nav-icon">
                      <Icon size={18} />
                    </span>
                    <span className="nav-text">{label}</span>
                    {badge && badges[badge] != null && (
                      <span className="badge">{badges[badge]}</span>
                    )}
                  </NavLink>
                </RailTip>
              ))}
            </nav>

            {data && (
              <div className="nav-group hide-collapsed">
                <div className="nav-label">This window</div>
                <div className="mini-stats">
                  <MiniStat label="Spend" value={miniSpend} />
                  <MiniStat label={miniResultLabel} value={miniResults.toLocaleString()} />
                  <MiniStat label="Recoverable" value={miniWaste} tone="serious" />
                </div>
                {urgent > 0 && (
                  <NavLink to={{ pathname: "findings", search }} className="alert-pill">
                    <TriangleAlert size={14} />
                    <span>{urgent} need attention</span>
                  </NavLink>
                )}
              </div>
            )}
          </div>

          <div className="sidebar-foot">
            <div className="user-row">
              <span className="avatar">{(user || "?").slice(0, 1).toUpperCase()}</span>
              <span className="nav-text user-name">{user}</span>
              <RailTip label="Sign out" collapsed={collapsed}>
                <button
                  className="icon-btn"
                  onClick={async () => {
                    await signOut();
                    navigate("/");
                  }}
                  aria-label="Sign out"
                >
                  <LogOut size={16} />
                </button>
              </RailTip>
            </div>
          </div>
        </aside>

        <div className="main">
          <header className="topbar">
            <div className="topbar-title">
              <h1>
                {isPortfolio
                  ? `${data?.accounts?.length ?? accountIds.length} accounts compared`
                  : account?.name || "…"}
              </h1>
              <p>
                {isPortfolio
                  ? (data?.accounts || []).map((a) => a.accountName).join(" · ") ||
                    accountIds.join(", ")
                  : accountId}
                {!isPortfolio && account?.business ? ` · ${account.business}` : ""}
                {data?.window?.since && data?.window?.until
                  ? ` · ${data.window.since} → ${data.window.until}`
                  : data?.window?.label
                    ? ` · ${data.window.label}`
                    : ""}
              </p>
            </div>

            <div className="topbar-actions">
              <DateRangePicker
                value={win}
                label={data?.window?.label || "Last 30 days"}
                onChange={setWindow}
              />
              <button className="btn" onClick={handleRefresh} disabled={loading}>
                <RefreshCw size={15} className={loading ? "spin" : undefined} />
                <span className="btn-label">{loading ? "Refreshing" : "Refresh"}</span>
              </button>
              {/* CSV export is per account; a portfolio has no single file. */}
              {!isPortfolio && (
                <a className="btn" href={api.exportUrl(accountId, win)}>
                  <Download size={15} />
                  <span className="btn-label">Export</span>
                </a>
              )}
            </div>
          </header>

          <div className="content">
            {error && (
              <div className="error-box" style={{ marginBottom: 16 }}>
                <strong>Could not load this account.</strong> {error.message}
                {error.detail?.code ? ` (Graph code ${error.detail.code})` : ""}
              </div>
            )}

            {/* A partial fetch silently drops findings and *raises* the score,
                so an incomplete audit must never look like a clean one. */}
            {warnings.length > 0 && (
              <div className="warn-box">
                <Info size={16} />
                <span>
                  <strong>This audit is incomplete.</strong>{" "}
                  {warnings.map((w) => w.source.replace(/_/g, " ")).join(", ")} could not be
                  fetched, so some findings are missing and the health score reads higher than
                  it should. Try Refresh — this is usually Meta rate-limiting.
                </span>
              </div>
            )}
            {/* Keyed on pathname so each page fades in on navigation. Short and
                shallow on purpose -- a dashboard that animates hard on every
                route change gets tiring to use. */}
            <AnimatePresence mode="wait">
              <motion.div
                key={location.pathname}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18, ease: "easeOut" }}
              >
                <Outlet />
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      </div>
    </Tooltip.Provider>
  );
}

function MiniStat({ label, value, tone }) {
  return (
    <div className="mini-stat">
      <span className="mini-stat-label">{label}</span>
      <span className={`mini-stat-value ${tone ? `tone-${tone}` : ""}`}>{value}</span>
    </div>
  );
}
