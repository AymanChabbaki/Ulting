import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../lib/api.js";
import { useSession } from "../App.jsx";
import { fromMinor, money } from "../lib/format.js";
import { LangSwitch, useT } from "../lib/i18n.jsx";

const STATUS = {
  1: { label: "Active", color: "var(--good)" },
  2: { label: "Disabled", color: "var(--critical)" },
  3: { label: "Unsettled", color: "var(--warning)" },
  7: { label: "Risk review", color: "var(--warning)" },
  8: { label: "Pending settlement", color: "var(--warning)" },
  9: { label: "Grace period", color: "var(--warning)" },
  101: { label: "Closed", color: "var(--text-muted)" },
};

export default function Accounts() {
  const t = useT();
  const [state, setState] = useState({ loading: true });
  const navigate = useNavigate();
  const { user, signOut } = useSession();

  useEffect(() => {
    api
      .accounts()
      .then((data) => setState({ loading: false, ...data }))
      .catch((error) => setState({ loading: false, error }));
  }, []);

  const expiry = state.token?.expiresAt ? new Date(state.token.expiresAt * 1000) : null;
  const daysLeft = expiry ? Math.round((expiry - Date.now()) / 86400000) : null;

  return (
    <div style={{ maxWidth: 860, margin: "0 auto", padding: "48px 20px" }}>
      <header
        style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16 }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <img src="/ulting-mark.png" alt="" style={{ width: 40, height: 40, objectFit: "contain" }} />
          <div>
            <h1 style={{ fontSize: 20 }}>{t("Ad accounts")}</h1>
            <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
              {t("Pick an account to audit and analyse.")}
            </p>
          </div>
        </div>
        <div style={{ textAlign: "right", fontSize: 12, color: "var(--text-muted)" }}>
          <LangSwitch />
          <div style={{ marginTop: 8 }}>{user}</div>
          <button
            className="btn"
            style={{ padding: "3px 9px", fontSize: 12, marginTop: 6 }}
            onClick={signOut}
          >
            {t("Sign out")}
          </button>
        </div>
      </header>

      {state.token && (
        <div
          className="card card-pad"
          style={{ marginTop: 22, display: "flex", gap: 20, flexWrap: "wrap", fontSize: 13 }}
        >
          <span style={{ color: "var(--text-muted)" }}>
            {t("App")} <strong style={{ color: "var(--text)" }}>{state.token.appName}</strong>
          </span>
          {daysLeft != null && (
            <span style={{ color: "var(--text-muted)" }}>
              {t("Token expires in")}{" "}
              <strong style={{ color: daysLeft < 7 ? "var(--critical)" : "var(--text)" }}>
                {t("{n} days", { n: daysLeft })}
              </strong>
            </span>
          )}
          <span style={{ color: "var(--text-muted)" }}>{t("{n} scopes", { n: state.token.scopes?.length })}</span>
        </div>
      )}

      {state.error && (
        <div className="error-box" style={{ marginTop: 22 }}>
          <strong>{t("Could not reach the Marketing API.")}</strong> {state.error.message}
        </div>
      )}

      <div className="grid" style={{ marginTop: 16 }}>
        {state.loading &&
          Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="card" style={{ height: 74, opacity: 0.5 }} />
          ))}

        {(state.accounts || []).map((account) => {
          const status = STATUS[account.account_status] || {
            label: `${t("Status")} ${account.account_status}`,
            color: "var(--text-muted)",
          };
          return (
            <button
              key={account.id}
              className="card card-pad"
              onClick={() => navigate(`/a/${account.id}`)}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 16,
                textAlign: "left",
                cursor: "pointer",
                border: "1px solid var(--border)",
              }}
            >
              <div style={{ minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
                  <span style={{ fontWeight: 600 }}>{account.name}</span>
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 5,
                      fontSize: 11,
                      fontWeight: 600,
                      color: status.color,
                    }}
                  >
                    <span className="dot" style={{ background: status.color }} />
                    {t(status.label)}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                  {account.id}
                  {account.business ? ` · ${account.business.name}` : ` · ${t("no business portfolio")}`}
                </div>
              </div>
              <div style={{ textAlign: "right", flexShrink: 0 }}>
                <div className="tnum" style={{ fontWeight: 600 }}>
                  {money(fromMinor(account.amount_spent), account.currency, { compact: true })}
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)" }}>{t("lifetime spend")}</div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
