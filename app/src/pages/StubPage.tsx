// StubPage — the page frame shown for every route Task 8+ has not built.
//
// House rules:
//   * Takes the page name and renders it as the page heading, followed by
//     the "coming in a later release" empty state.
//   * Pure render — no fetch, no network, no timers.
//   * Colors come from theme tokens only — never raw hexes.
export interface StubPageProps {
  /** Display name of the page, e.g. "Signals". */
  name: string;
}

export function StubPage({ name }: StubPageProps) {
  return (
    <section className="px-6 py-6">
      <h1 className="text-lg font-semibold text-fg">{name}</h1>
      <p className="mt-4 text-sm text-muted">coming in a later release</p>
    </section>
  );
}
