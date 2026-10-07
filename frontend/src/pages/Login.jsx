import { useState } from "react";
import { api } from "../lib/api.js";
import { useSession } from "../App.jsx";
import { LangSwitch, useT } from "../lib/i18n.jsx";

export default function Login() {
  const { signIn } = useSession();
  const t = useT();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api.login(username, password);
      signIn(result.user);
    } catch (cause) {
      setError(cause.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <div className="login-head">
          <img className="login-logo" src="/ulting-logo.png" alt="Ulting" />
          <p className="login-sub">{t("Meta campaign audit & analysis")}</p>
          <LangSwitch className="login-lang" />
        </div>

        {error && <div className="error-box">{error}</div>}

        <label className="field">
          <span className="field-label">{t("Username")}</span>
          <input
            className="input"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            autoComplete="username"
          />
        </label>

        <label className="field">
          <span className="field-label">{t("Password")}</span>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
          />
        </label>

        <button className="btn btn-primary" style={{ width: "100%", justifyContent: "center" }} disabled={busy}>
          {busy ? t("Signing in…") : t("Sign in")}
        </button>

        <p className="login-hint">
          {t("Credentials come from DASHBOARD_USER / DASHBOARD_PASSWORD in .env.local")}
        </p>
      </form>
    </div>
  );
}
