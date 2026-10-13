// app/tests/scaffold.test.tsx
import { render, screen } from "@testing-library/react";
import App from "../src/App";
import { stubReadOnlyFetch } from "./sse_stub";

// The wired Dashboard opens /events and probes /healthz + /readyz on mount
// (Task 11 fix round 1) — stub them so the mount probes don't resolve
// after the test body (see tests/sse_stub.ts).
stubReadOnlyFetch();

test("app shell mounts", () => {
  render(<App />);
  expect(screen.getByText("ALT RADAR")).toBeInTheDocument();
});
