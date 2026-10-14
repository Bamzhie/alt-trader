// ConnectionBanner — surfaces the SSE connection lifecycle.
//
// House rules:
//   * "starting" shows "starting — first sweep in progress" (the service is
//     alive but not yet ready).
//   * "connected" renders nothing at all.
//   * "error" shows a contained fallback: the shell stays usable, the
//     message states the condition and that a retry is in progress.
//   * Colors come from theme tokens only — never raw hexes.
import type { ConnectionState } from "../store/dashboard";

export interface ConnectionBannerProps {
  connection: ConnectionState;
}

export function ConnectionBanner({ connection }: ConnectionBannerProps) {
  if (connection === "connected") return null;

  const isError = connection === "error";

  return (
    <div
      role={isError ? "alert" : "status"}
      className={
        isError
          ? "border-b border-short bg-bg-elevated px-6 py-2 text-sm text-short"
          : "border-b border-border bg-bg-elevated px-6 py-2 text-sm text-muted"
      }
    >
      {isError
        ? "connection error — retrying automatically; showing the last data received"
        : "starting — first sweep in progress"}
    </div>
  );
}
