import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import { LangProvider } from "./lib/i18n.jsx";
// Self-hosted so the dashboard has no network dependency for its type.
import "@fontsource-variable/plus-jakarta-sans";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <LangProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </LangProvider>
  </React.StrictMode>
);
