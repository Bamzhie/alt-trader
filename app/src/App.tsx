// App — the neutral shell: top bar, connection banner, and a single Status page.
//
// House rules:
//   * One page; no state-routing router needed.
//   * Connection state is probed live from the service on mount; the shell
//     shows a banner while unreachable and the Status page while connected.
//   * Colors come from theme tokens only — never raw hexes.
import { useEffect, useState, type ReactNode } from "react";
import { createClient, type ApiClient } from "./api/client";
import { ConnectionBanner } from "./components/ConnectionBanner";
import { TopBar } from "./components/TopBar";
import { StatusPage } from "./pages/Status";

export interface AppProps {
  apiBaseUrl?: string;
  serviceToken?: string;
}

type ConnectionState = "starting" | "connected" | "error";

function useConnectionState(client: ApiClient): ConnectionState {
  const [state, setState] = useState<ConnectionState>("starting");
  useEffect(() => {
    let mounted = true;
    const ping = async () => {
      const alive = await client.healthz();
      if (!mounted) return;
      setState(alive ? "connected" : "error");
    };
    void ping();
    const id = setInterval(ping, 5000);
    return () => {
      mounted = false;
      clearInterval(id);
    };
  }, [client]);
  return state;
}

export default function App({
  apiBaseUrl = "/api",
  serviceToken = import.meta.env.DEV ? "dev-token" : "",
}: AppProps = {}): ReactNode {
  const client = createClient({ baseUrl: apiBaseUrl, token: serviceToken });
  const connection = useConnectionState(client);

  return (
    <div className="flex min-h-screen flex-col bg-bg text-fg">
      <TopBar />
      <ConnectionBanner connection={connection} />
      <main className="min-w-0 flex-1">
        <StatusPage client={client} connection={connection} />
      </main>
    </div>
  );
}
