# SDD ledger — plan: docs/superpowers/plans/2026-10-14-frontend-dashboard.md

Spec: docs/superpowers/specs/2026-10-14-frontend-dashboard-design.md (d40820f)
Plan: docs/superpowers/plans/2026-10-14-frontend-dashboard.md (9340d6b)

## Pre-flight conflict scan

Rows — task pairs sharing a file or interface:

| Pair | What one produces / other consumes | Found |
|---|---|---|
| T1 → T2 | T1 scaffold (package.json, vitest tripwire) / T2 adds dev/ files + python tests | Clean — separate trees; tripwire only guards js tests |
| T1 → T3 | T1 vitest config / T3 tests use `vi.stubGlobal("fetch")` | Clean — plan T3 says stub, no network |
| T3 → T4 | T3 `Event`/`Snapshot` types / T4 sse hook consumes them | Clean — types.ts is the single source |
| T3 → T8/T9/T10 | T3 client methods (snapshot/bars/metrics/tradeplan) / later pages call them | Clean — signatures pinned in T3 Interfaces |
| T5 → T7/T8/T9/T10 | T5 store actions / pages consume | Clean — action names pinned in T5 |
| T6 → T8/T9 | T6 EvidenceBar/Sparkline/StatCard/SignalPill / dashboard uses | Clean |
| T7 → T8 | T7 Dashboard.tsx placeholder / T8 fills it | Clean — T7 notes "Task 8 fills it" |
| T8 → T9/T10 | T8 Dashboard placeholders / T9+T10 fill right column + bottom row | Clean |

Per-task self-agreement:

| Task | Tests vs code vs files | Found |
|---|---|---|
| T1 | test asserts App renders wordmark; code is placeholder App | Clean |
| T2 | python tests assert exact statuses; mock implements routes | Clean |
| T3 | tests cast `"1m" as never`; type omits 1m | Clean |
| T4 | tests use fake EventSource; code is fetch-based reader | Clean |
| T5 | tests assert zero fetch; store is pure | Clean |
| T6 | tests assert null→unavailable; components render accordingly | Clean |
| T7 | tests click nav; shell routes via usePage | Clean |
| T8 | banned-words test includes "leverage"/"liquidation" | Clean |
| T9 | tests assert no leverage text; detail omits leverage | Clean |
| T10 | tests assert three regions render | Clean |
| T11 | full suite + build + manual checklist | Clean |

Scan clean. No pre-flight rulings.
Task 2: fix round 1/5 (4 addressed, 0 open; commits 09053aa..fcf2b3a) — re-review verified all four ADDRESSED with passing regression tests; no new breakage.
Task 2: complete (commits 1d03727..fcf2b3a, review clean after 1 fix round)
Task 3: fix round 1/5 (1 addressed, 0 open; commits 6be4583..5c3889c) — re-review verified: Setup.name now optional (types.ts:51-60), misleading comment corrected, setup(id) fixture matches the wire (test_client.test.ts:363-388). No new breakage.
Task 3: complete (commits fcf2b3a..5c3889c, review clean after 1 fix round)
Task 4: complete (commits 5c3889c..9be1ace, review clean) — minors deferred: dead /snapshot stub in test, test-3 replay half inferred not tested, resync reason unused, per-event setLastEventId renders, no backoff jitter/error channel, backoff growth untested.
Task 5: fix round 1/5 (1 addressed, 0 open; commits 404ca40..d43858f) — re-review reproduced RED evidence independently: fresh-module guard (test_store.test.ts:62-80) fails on regressed default while 17 fixture tests pass; store defaults unchanged.
Task 5: complete (commits 9be1ace..d43858f, review clean after 1 fix round)
Task 6: complete (commits d43858f..0887caf, review clean) — minors deferred: StatCard delta colors by leading '-' (bare "0" renders green; carried to Task 8), failed-criteria vs null-input chips visually identical, Math.min spread on huge arrays theoretical, flat-values span===0 path untested, no-hex guard grep-only.
Task 7: complete (commits 0887caf..e8984be, review clean) — minors deferred: SCANNING pill static even on connection error, apiStatusText switch no default (closed union today), Page type re-export indirection.
Task 8: fix round 1/5 (2 addressed, 0 open; commits f54da7c..c0623da) — re-review independently reproduced RED on pre-fix code: StrictMode + deferred-bars regression tests fail without the fix, pass with it; fix is the fetched.add relocation (post-resolution, cancelled-gated).
Task 8: complete (commits e8984be..c0623da, review clean after 1 fix round) — minors deferred: transient /bars failure permanently marks coin fetched with [] (pre-existing sharp edge), key={line} collision risk, role=row missing, DEV_TOKEN bundled in prod build, test-inventory drift.
Task 9: fix round 1/5 (5 addressed, 0 open; commits 95438c8..6b3d288) — re-review verified: EMA overlays removed w/ source-guard test (readFileSync not.toMatch), horizon assertion two-sided, coin-switch reset tested w/ deferred reads, overview sparklines wired from real closes, placeholderBars memoized. New MINOR regression deferred: CoinDetail.tsx:151-196 unconditional reset in barTimeframe-dependent effect blanks header + triple-refetches on every timeframe click.
Task 9: complete (commits c0623da..6b3d288, review clean after 1 fix round) — minors deferred (final-review triage): timeframe-click header flash + refetch (above), 404 vs network indistinguishable, vacuous null-RSI assertion, tier missing from detail banned-words guard, jsdom getContext console noise, dead chartRef/seriesRef, lockfile spec mismatch; Task 9-brief EMA props line now stale vs ruling.
Task 10: complete (commits 6b3d288..515a271, review clean) — both flagged concerns adjudicated correct by review: High Volume tab renders "volume ranking unavailable" (no volume field in contract; GUI_MANUAL.md:667 prescribes the same gap handling), test re-scope is a legitimate correction (tbody tr IS the signals table; movers legitimately list SOL). Minors deferred: MarketMovers dead zero-branch + double rowsFor + tab alias, timeAgo duplicated across RecentAnalysis/ActivityLog, aria-pressed vs role=tab inconsistency.
Task 11: fix round 1/5 (1 addressed, 0 open; commits 036d1d1..7308b51) — re-review verified: useEvents + setConnection wired in Dashboard.tsx (readyz-driven connection per §2, NOT stream-open; onEvent/onResync -> coalescing refetch per §5), 6 new no-network integration tests in test_sse_wiring.test.tsx (42/42 in affected files), README blemishes 2+3 fixed, blemish 1 verified display artifact.
Task 11: complete (commits 515a271..7308b51, review clean after 1 fix round)
FINAL REVIEW: READY WITH FIXES (9340d6b..7308b51, 18 commits; 172 vitest + 48 mock tests green, typecheck/lint/build clean). All ~30 deferred minors triaged can-ride except two Important promoted to fix-before-merge: (1) CoinDetail.tsx:151-196 barTimeframe dependency resets all four state groups per timeframe click (header flash + triple refetch), (2) CoinDetail.tsx:220,311-317 failed setup fetch indistinguishable from no-setup (fabricates 'no rule criteria met' from a transport failure). All four controller rulings confirmed sound (EMA removal, High Volume unavailable copy, gen_fixtures.py, /api rewrite).
FINAL FIX WAVE: dispatched 7308b51 — the two Important fixes + README test count 166->172; one scoped re-review follows.
FINAL FIX WAVE: complete (commits 7308b51..7cff29b) — re-review verified: effects split by read scope (CoinDetail.tsx:176-234; timeframe click clears bars only, no coin-scoped refetch), setup failure discriminated via ApiError code (NOT_FOUND -> no-setup copy; else 'setup unavailable' + message), README count 176 verified by test-declaration count. No new breakage. Branch READY TO MERGE.
Task 9: Ruling: client-side EMA overlays REMOVED from the chart (controller ruling at review, applied in fix round 1) — the spec's "frontend never computes indicators" outranks the brief's pinned ema50/ema200 props; the client-computed series disagreed numerically with /metrics (wrong seed vs proto/indicators.py SMA-of-first-n, truncated window) while sharing the label, which is misleading on a trading board. Chart renders candles + volume only until the real service exposes EMA series via /bars. Cost if wrong: chart temporarily lacks EMA overlays; a source-guard test (readFileSync not.toMatch) pins the decision and will need amending when backend-supplied line series land.
Task 1: complete (commits 9340d6b..fcf61e7, tests: npm run test →    Duration  1.18s (transform 56ms, setup 76ms, collect 83ms, tests 37ms, environment 427ms, prepare 236ms))

Task 1: Ruling: vite pinned ^5.4.11 (not ^6) — vitest 2.1.9 hard-depends on vite 5.4.21; root vite 6 made vitest resolve a second nested vite copy and `tsc --noEmit` failed on the Plugin type mismatch. Vite 5 is the supported pairing; cost if wrong: a future vitest major bump requires revisiting this pin.
Task 1: Ruling: exact 12 theme hexes chosen by implementer — the plan delegates this ("exact 12 values chosen by implementer from the spec/mockup"); no hexes exist in either spec or mockup docs, so a deep-navy #0b0e17 family was authored (bg #0b0e17, bg-elevated #0f1420, card #151b2b, card-hover #1b2338, border #232c42, border-strong #2f3a57, fg #e7ecf5, muted #8a94ab, accent #4f8cff, long #2fbf71, short #e5484d, chart-grid #1e2637). Cost if wrong: cosmetic restyle only — every consumer references var(--*) tokens, never hexes.
Task 1: Ruling: `server.host: "127.0.0.1"` added to vite.config.ts — global constraint says loopback-only; vite defaults to `localhost` which on this host resolved to ::1 (127.0.0.1 refused). Explicit bind satisfies the constraint on both addresses. Cost if wrong: none (dev-only).
Task 1: Ruling: eslint.config.js added though not in the brief's Files list — global constraints require "lint clean", which needs a lint config; npm script `lint` (eslint --max-warnings 0) is provided. Cost if wrong: one extra config file later tasks may extend.
Task 1: Note: `npm install` warnings — npm's install-scripts policy blocked esbuild's postinstall; the platform binary works anyway (verified via build + tests). No action taken.
Task 1: fix round 1/5 (3 addressed, 0 open; commits fcf61e7..1d03727) — re-review verified: Tailwind directives in theme.css:3-5 (build CSS 5.70 kB, utilities emit), tripwire patches net.createConnection + http/https (10/10 tripwire tests), scaffold unaffected. No new breakage.
Task 1: complete (commits 9340d6b..1d03727, review clean after 1 fix round)

## Task 1 fix round 1

Fixed all 3 review findings from the Task 1 review (commit fcf61e7). Commit: 1d03727.

**What changed**

1. **Tailwind zero-CSS (Critical)** — `app/src/theme.css` now opens with `@tailwind base; @tailwind components; @tailwind utilities;` before the `:root` token block. Without the directives, PostCSS processed theme.css as plain CSS and emitted none of the mapped utilities; `npm run build` produced a 438-byte stylesheet.
2. **Tripwire http/https leak (Critical)** — `app/tests/setup.ts` now also patches, in addition to `net.connect` and `dgram`:
   - `net.createConnection` — a separate module export of the same underlying connect function that `net.connect` was missing (they are distinct properties even though `net.connect === net.createConnection` at load time). Node's http/https clients drive `createConnection`, so patching only `connect` left a real egress path.
   - `http.request`/`http.get` and `https.request`/`https.get` — host extracted from the options object (`host ?? hostname`) or parsed from a URL-string first argument. Note: `new URL()` parse failures are caught, but the loopback assertion runs outside that catch so the tripwire's own throw is never swallowed (first attempt had this bug and one test stayed red).
3. **No tripwire test (Important)** — new `app/tests/tripwire.test.ts` (TDD): written first and run to failure — 7 of 10 tests failed (`net.createConnection`, all 6 http/https entry points threw nothing); after the setup.ts patch, all 10 pass, plus the scaffold test. Covers net.connect (options + port/host forms), net.createConnection, http.request/get (options + URL string), https.request/get (options + URL string), and a loopback-allowed control. Uses 192.0.2.1 (RFC 5737 TEST-NET-1) as the non-loopback host — unreachable even if the tripwire misses, and any returned handle is destroyed.

**Verification**

- Command: `cd app && npm run test && npm run typecheck && npm run lint && npm run build`
- Output: `Test Files 2 passed (2) / Tests 11 passed (11)`; typecheck exit 0; lint exit 0; build `dist/assets/index-CysZOttW.css 5.70 kB` with utilities `.min-h-screen`, `.bg-bg`, `.text-fg`, `.border-border` all present in the generated CSS (previously 0 of 4).
- Banned-term scan over app/src + app/tests (leverage/liquidation/confidence/probability/tier/"1m"): no matches.

**Concerns**

- The http/https patches wrap request/get at the module level; code that captured a pre-setup reference to `http.request` would bypass them — acceptable here since setup.ts loads first via vitest `setupFiles`, but later tasks (T3 client, T4 SSE) should import node:http/https lazily rather than caching the function.
- Tripwire guards only the shapes tests/setup.ts can see (first-argument host); a hypothetical `agent` with a pre-configured non-loopback `createConnection` is not intercepted. Out of scope for Task 1.
