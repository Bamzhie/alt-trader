// app/tests/scaffold.test.tsx
import { render, screen } from "@testing-library/react";
import App from "../src/App";

test("app shell mounts", () => {
  render(<App />);
  expect(screen.getByText("ALT RADAR")).toBeInTheDocument();
});
