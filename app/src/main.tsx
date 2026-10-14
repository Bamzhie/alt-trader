import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { invoke } from "@tauri-apps/api/core";
import App from "./App";
import "./theme.css";

const SERVICE_URL = "http://127.0.0.1:18741";
const DEVELOPMENT_TOKEN = import.meta.env.DEV ? "dev-token" : "";

function isTauriWindow(): boolean {
  return "__TAURI_INTERNALS__" in window;
}

export default function DesktopBootstrap() {
  const [token, setToken] = useState<string | null>(
    isTauriWindow() ? null : DEVELOPMENT_TOKEN,
  );
  const [tokenError, setTokenError] = useState<string | null>(null);

  useEffect(() => {
    if (!isTauriWindow()) return;
    let active = true;
    invoke<string>("get_service_token")
      .then((serviceToken) => {
        if (active) setToken(serviceToken);
      })
      .catch((error: unknown) => {
        if (active) {
          setTokenError(
            error instanceof Error ? error.message : "service token unavailable",
          );
        }
      });
    return () => {
      active = false;
    };
  }, []);

  if (tokenError) {
    return (
      <main className="min-h-screen bg-bg p-8 text-fg">
        <h1 className="text-lg font-semibold">ALT RADAR service access unavailable</h1>
        <p className="mt-3 text-sm text-muted">{tokenError}</p>
        <p className="mt-3 text-sm text-muted">
          Check the service with <code>systemctl --user status altradar.service</code>.
          View logs with <code>journalctl --user -u altradar.service -n 100 --no-pager</code>.
        </p>
      </main>
    );
  }

  if (token === null) {
    return (
      <main className="min-h-screen bg-bg p-8 text-sm text-muted">
        Connecting to ALT RADAR service…
      </main>
    );
  }

  return (
    <App
      apiBaseUrl={isTauriWindow() ? SERVICE_URL : "/api"}
      serviceToken={token}
    />
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <DesktopBootstrap />
  </StrictMode>,
);
