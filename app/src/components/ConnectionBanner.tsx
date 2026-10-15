// ConnectionBanner — surfaces the SSE connection lifecycle.
//
// House rules:
//   * "starting" shows while the local API event stream is connecting.
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
        ? "Connection error: ALT RADAR service unavailable — retrying. Check: systemctl --user status altradar.service; journalctl --user -u altradar.service -n 100 --no-pager"
        : "connecting to ALT RADAR service…"}
    </div>
  );
}