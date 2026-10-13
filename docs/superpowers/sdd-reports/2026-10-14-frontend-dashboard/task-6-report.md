# Task 6 Report: Shared components — LetterBadge, Sparkline, EvidenceBar, StatCard, SignalPill

**Status: DONE** — commit `0887caf`, 18 new tests + 78/78 full suite green, typecheck + lint clean.

## What was implemented

Five pure-render components in `app/src/components/`, all consuming Task 1
theme tokens (Tailwind token classes only — zero raw hexes, zero network,
zero props beyond the documented signatures):

- **LetterBadge.tsx** — circular badge drawing the symbol's first letter
  (uppercased; empty symbol → "?"), sized via optional `size` prop
  (default 32). `aria-hidden` decorative glyph (always paired with the
  ticker/name per the design spec).
- **Sparkline.tsx** — inline SVG polyline from closed-bar values.
  Stroke = `currentColor` so the `text-long` / `text-short` token class
  drives direction color. Guards: empty array → empty frame (no line, no
  fabricated data); single value → lone dot; flat span (max === min) →
  mid-height flat line instead of divide-by-zero. Dimensions are internal
  constants (96×28) — the brief forbids props beyond the documented
  signature.
- **EvidenceBar.tsx** — criteria chips (passed = `text-long`, failed =
  `text-muted`) + one chip per input; a `null` input renders the literal
  word "unavailable" (never 0, never inferred); available inputs render a
  locale-free formatted value. Contains no numeric confidence/probability.
  Reuses `SetupCriterion` from `api/types` (exact documented shape).
- **StatCard.tsx** — label / value / optional delta / optional footnote.
  `delta.available === false` renders "unavailable" even if a stale value
  string rode along (flag is authoritative); no delta prop → no delta row.
- **SignalPill.tsx** — LONG = `text-long`, SHORT = `text-short`; typed via
  the `Direction` union from `api/types`.

## TDD evidence

### RED — test written first, verified failing

```
$ cd app && npm run test -- tests/test_components.test.tsx
FAIL  tests/test_components.test.tsx
Error: Failed to resolve import "../src/components/EvidenceBar" from
"tests/test_components.test.tsx". Does the file exist?
Test Files  1 failed (1)
```

### GREEN — after implementation

```
$ npm run test -- tests/test_components.test.tsx
 ✓ tests/test_components.test.tsx (18 tests) 105ms
 Test Files  1 passed (1)   Tests  18 passed (18)

$ npm run typecheck    # tsc --noEmit — clean, no output
$ npm run lint         # eslint . --max-warnings 0 — clean, no output
$ npm test             # full suite
 ✓ tests/test_components.test.tsx (18 tests)
 ✓ tests/test_store.test.ts (18)  ✓ tests/test_client.test.ts (26)
 ✓ tests/test_sse.test.ts (5)     (+ tripwire tests)
 Test Files  6 passed (6)   Tests  78 passed (78)
```

Test file `app/tests/test_components.test.tsx` follows house style: the
brief's Step-1 tests verbatim first, then added coverage — LetterBadge
(first letter, empty-symbol fallback, size→dimensions), Sparkline
(one point per value, direction token colors, single value), EvidenceBar
(passed/failed token colors, available value + null → unavailable, no
confidence/probability/conviction language in rendered output), StatCard
(label/value/footnote, available delta, no-delta), SignalPill SHORT, and a
cross-component guard asserting zero `fetch` calls on mount (on top of the
global socket tripwire).

## Files changed

- `app/src/components/LetterBadge.tsx` (new)
- `app/src/components/Sparkline.tsx` (new)
- `app/src/components/EvidenceBar.tsx` (new)
- `app/src/components/StatCard.tsx` (new)
- `app/src/components/SignalPill.tsx` (new)
- `app/tests/test_components.test.tsx` (new)

Commit: `0887caf feat(components): badge, sparkline, evidence bar, stat card, signal pill`

## Self-review findings

1. **Avoided Tailwind alpha modifiers** (`bg-accent/15` style): with
   var()-based color tokens, Tailwind 3 cannot inject alpha reliably —
   used plain token classes (`border border-border bg-bg-elevated
   text-accent`) so every color resolves to a real `var(--*)`.
2. **No extra props**: Sparkline dimensions are internal constants, per
   the brief's "no props beyond the documented signatures".
3. **Locale-free formatting** in EvidenceBar (`Math.round(v*1e4)/1e4` +
   `String()`) — deterministic across ICU builds, keeps tests stable.
4. **StatCard delta coloring** is a presentational string-prefix check
   only ("-" → short token) — no indicator math, no inference.
5. **Null-safety**: StatCard handles both `delta` absent and
   `available: true` + `value: null`; Sparkline handles 0/1/flat-value
   arrays; LetterBadge handles empty/whitespace symbols.

## Concerns

- None blocking. One note for Task 8 (StatCards): StatCard colors the
  delta by a leading "-" in the value string — a bare "0" (no change)
  renders with the long token. Purely presentational; the delta strings
  are produced upstream, so Task 8 should emit explicit "+"/"-" prefixes
  (e.g. "0" for flat) if neutral coloring is preferred.
