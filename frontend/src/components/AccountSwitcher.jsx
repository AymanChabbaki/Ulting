import { useEffect, useMemo, useState } from "react";
import * as Popover from "@radix-ui/react-popover";
import { useNavigate } from "react-router-dom";
import { Check, ChevronsUpDown, Layers, Search } from "lucide-react";

import { useAccounts } from "../App.jsx";
import { fromMinor, money } from "../lib/format.js";

/**
 * Account switcher.
 *
 * Two interactions in one control, which is the whole point: clicking a row
 * switches to that account immediately (a client-side navigate, so the page
 * never reloads), while the checkbox on the left builds a multi-account
 * selection that opens the comparison view. Neither bounces through the
 * account-list page.
 *
 * Selection is held locally and only committed on navigate, so ticking three
 * boxes fires one request rather than three.
 */

const STATUS_TONE = {
  1: "var(--good)",
  2: "var(--critical)",
  3: "var(--warning)",
  7: "var(--warning)",
  8: "var(--warning)",
  9: "var(--warning)",
  101: "var(--text-muted)",
};

export default function AccountSwitcher({ activeIds = [], search = "" }) {
  const { accounts, loading } = useAccounts();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [checked, setChecked] = useState(activeIds);

  // Re-seed from the route each time it opens: the URL is the source of truth,
  // and an abandoned selection must not persist into the next open.
  useEffect(() => {
    if (open) {
      setChecked(activeIds);
      setQuery("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return accounts;
    return accounts.filter(
      (a) =>
        a.name?.toLowerCase().includes(needle) ||
        a.id.toLowerCase().includes(needle) ||
        a.business?.name?.toLowerCase().includes(needle)
    );
  }, [accounts, query]);

  const suffix = search ? `?${search}` : "";

  function go(ids) {
    if (!ids.length) return;
    const path = ids.length === 1 ? `/a/${ids[0]}` : `/p/${ids.join(",")}`;
    setOpen(false);
    navigate(`${path}${suffix}`);
  }

  function toggle(id) {
    setChecked((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  const current = accounts.filter((a) => activeIds.includes(a.id));
  const label =
    current.length > 1
      ? `${current.length} accounts`
      : current[0]?.name || (loading ? "Loading…" : "Select account");
  const sub =
    current.length > 1
      ? current.map((a) => a.name).join(", ")
      : current[0]?.business?.name || current[0]?.id || "";

  const changed =
    checked.length !== activeIds.length || checked.some((id) => !activeIds.includes(id));

  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button className="nav-item account-switch" aria-label="Switch ad account">
          <span className="nav-icon">
            {current.length > 1 ? <Layers size={18} /> : <ChevronsUpDown size={18} />}
          </span>
          <span className="nav-text account-text">
            <span className="account-name">{label}</span>
            <span className="account-meta">{sub}</span>
          </span>
        </button>
      </Popover.Trigger>

      <Popover.Portal>
        <Popover.Content className="acct-panel" side="right" align="start" sideOffset={10}>
          <div className="acct-search">
            <Search size={14} />
            <input
              className="acct-search-input"
              placeholder="Search accounts…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              autoFocus
            />
          </div>

          <div className="acct-list">
            {filtered.map((account) => {
              const isChecked = checked.includes(account.id);
              const isActive = activeIds.includes(account.id);
              return (
                <div key={account.id} className={`acct-row ${isActive ? "is-active" : ""}`}>
                  <button
                    className={`acct-check ${isChecked ? "on" : ""}`}
                    onClick={() => toggle(account.id)}
                    aria-label={`${isChecked ? "Deselect" : "Select"} ${account.name}`}
                    aria-pressed={isChecked}
                  >
                    {isChecked && <Check size={12} strokeWidth={3} />}
                  </button>

                  {/* Clicking the body switches straight to that one account. */}
                  <button className="acct-main" onClick={() => go([account.id])}>
                    <span className="acct-name">
                      <span
                        className="dot"
                        style={{
                          background: STATUS_TONE[account.account_status] || "var(--text-muted)",
                        }}
                        title={`Status ${account.account_status}`}
                      />
                      {account.name}
                    </span>
                    <span className="acct-meta">
                      {account.business?.name || account.id}
                    </span>
                  </button>

                  <span className="acct-spend">
                    {money(fromMinor(account.amount_spent), account.currency, { compact: true })}
                  </span>
                </div>
              );
            })}
            {!filtered.length && <div className="acct-empty">No accounts match.</div>}
          </div>

          <div className="acct-foot">
            <span className="acct-count">
              {checked.length} selected
              {checked.length > 1 && " · compared side by side"}
            </span>
            <button
              className="btn btn-primary"
              disabled={!checked.length || !changed}
              onClick={() => go(checked)}
            >
              {checked.length > 1 ? `Compare ${checked.length}` : "Open"}
            </button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
