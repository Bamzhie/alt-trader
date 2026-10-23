// ConnectionBanner — surfaces whether this desktop shell can reach the
// loopback service.
//
// House rules:
//   * "starting" shows while the first liveness probe is in flight.
//   * "connected" renders nothing at all.
//   * "error" shows a contained fallback: the shell stays usable and the
//     message names the systemd unit to inspect.
//   * Colors come from theme tokens only — never raw hexes.
import type { ReactNode } from "react";

export type ConnectionState = "starting" | "connected" | "error";

export interface ConnectionBannerProps {
  connection: ConnectionState;
}

export function ConnectionBanner({
  connection,
}: ConnectionBannerProps): ReactNode {
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
        ? "Connection error: ALT RADAR service unavailable. Check: systemctl --user status altradar.service; journalctl --user -u altradar.service -n 100 --no-pager"
        : "connecting to ALT RADAR service…"}
    </div>
  );
}
