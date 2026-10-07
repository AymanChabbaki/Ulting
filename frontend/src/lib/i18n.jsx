import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import FR from "./fr.js";

/**
 * English / French interface.
 *
 * The English text is the key: `t("Campaigns")` returns "Campagnes" in
 * French and the text itself in English, so a string nobody translated yet
 * still reads correctly instead of showing a key like `nav.campaigns`.
 *
 * `{name}` placeholders are filled from the second argument:
 *   t("{n} campaigns", { n: 4 })  ->  "4 campagnes"
 *
 * The choice is remembered per browser. Number and money formatting follow
 * it too (see format.js), and it is sent to the backend where text is
 * generated there -- the strategist's answers and the PDF report.
 */

export const LANGS = ["en", "fr"];
const STORAGE_KEY = "ulting.lang";

// Module-level copy for code that formats outside React (format.js).
let current = "en";
export const currentLang = () => current;

function initial() {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (LANGS.includes(saved)) return saved;
  } catch {
    /* private mode: fall through */
  }
  return navigator.language?.toLowerCase().startsWith("fr") ? "fr" : "en";
}

function fill(text, vars) {
  if (!vars) return text;
  return text.replace(/\{(\w+)\}/g, (m, k) => (vars[k] === undefined ? m : String(vars[k])));
}

/** Translate outside a component (rare; components should use useT). */
export function tr(text, vars) {
  const out = current === "fr" ? FR[text] ?? text : text;
  return fill(out, vars);
}

const LangContext = createContext({ lang: "en", setLang: () => {}, t: (s, v) => fill(s, v) });

export function LangProvider({ children }) {
  const [lang, setLangState] = useState(() => {
    current = initial();
    return current;
  });

  const setLang = useCallback((next) => {
    current = next;
    setLangState(next);
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* private mode */
    }
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const value = useMemo(
    () => ({
      lang,
      setLang,
      t: (text, vars) => fill(lang === "fr" ? FR[text] ?? text : text, vars),
    }),
    [lang, setLang]
  );

  return <LangContext.Provider value={value}>{children}</LangContext.Provider>;
}

export const useLang = () => useContext(LangContext);
export const useT = () => useContext(LangContext).t;

/** The EN | FR switch. */
export function LangSwitch({ className = "" }) {
  const { lang, setLang } = useLang();
  return (
    <div className={`lang-switch ${className}`} role="group" aria-label="Language">
      {LANGS.map((l) => (
        <button
          key={l}
          type="button"
          className={lang === l ? "active" : ""}
          aria-pressed={lang === l}
          onClick={() => setLang(l)}
        >
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
