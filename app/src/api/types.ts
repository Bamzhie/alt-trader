// Types mirroring the documented contract (mock_service.py, which itself
// mirrors docs/superpowers/specs/2026-10-14-tauri-desktop-design.md §1).
//
// HARD RULES (enforced across the frontend):
//   * READ-ONLY — no order/keys/execution fields exist in any type here.
//   * No confidence / probability / tier field anywhere.
//   * No 1m timeframe — BarTimeframe is 5m | 15m | 1H | 4H | 1D only.
//   * No leverage or liquidation field anywhere.
//   * Timestamps are UTC-seconds integers.
//   * Unavailable values are explicit nulls, never zeros and never guesses.

/** Every response body is wrapped in this envelope. */
export interface Envelope<T> {
  ok: boolean;
  data: T | null;
  error: { code: string; message: string } | null;
}

/** Error codes the client maps onto ApiError.code. */
export type ErrorCode =
  | "UNAUTH"
  | "BAD_REQUEST"
  | "NOT_FOUND"
  | "UNAVAILABLE"
  | "RATE_LIMITED"
  | "NETWORK";

/** Supported bar timeframes — note the deliberate absence of "1m". */
export type BarTimeframe = "5m" | "15m" | "1H" | "4H" | "1D";

/** Setup direction (no LONG/SHORT sizing — the frontend is read-only). */
export type Direction = "LONG" | "SHORT";

/** Setup lifecycle state, as accepted by /setups?state=. */
export type SetupState = "active" | "emitted" | "expired" | "invalidated" | "resolved";

/** Indicator inputs carried on a Setup; explicit null when unavailable. */
export interface SetupInputs {
  rsi: number | null;
  macd: number | null;
  ema50: number | null;
  ema200: number | null;
  volume_pct: number | null;
}

export interface SetupCriterion {
  name: string;
  passed: boolean;
}

/** One setup as returned by /setups and /setup/:id. */
export interface Setup {
  id: string;
  coin: string;
  /**
   * Coin display name. Present ONLY on the /snapshot route (mock_service.py
   * _snapshot() enriches it); /setups and /setup/:id attach just
   * tradeplan_id, so treat it as possibly absent everywhere else.
   */
  name?: string;
  direction: Direction;
  horizon: string;
  state: SetupState;
  emitted_ts: number;
  expires_ts: number;
  price: number;
  change_24h_pct: number | null;
  rule: string;
  criteria: SetupCriterion[];
  inputs: SetupInputs;
  /** Names of inputs that did not arrive for this sweep — explicit, never inferred. */
  unavailable_inputs: string[];
  analysis: string[];
  /** Trade plan id for the setup's coin, when one exists. */
  tradeplan_id: string | null;
}

/** Result of /setups pagination. */
export interface SetupsPage {
  setups: Setup[];
  nextCursor: string | null;
}

/** Query parameters accepted by ApiClient.setups(). */
export interface SetupsQuery {
  state?: SetupState;
  limit?: number;
  cursor?: string;
}

export interface SnapshotComparison24h {
  /** True only when a prior snapshot exists at or before now-24h. */
  available: boolean;
  reason: string | null;
  prior_ts: number | null;
  delta_active_setups: number | null;
  delta_new_setups_24h: number | null;
}

export interface SnapshotHistoryRow {
  ts: number;
  eligible_count: number;
  evaluated_count: number;
  active_setups: number;
  new_setups_24h: number;
  monitoring_coverage: number;
  covers_24h: boolean;
}

export interface CoinWithoutSetup {
  coin: string;
  name: string;
  blocking_reason: string;
  price?: number | null;
  change_24h_pct?: number | null;
  quote_vol_24h?: number | null;
  last_seen?: number | null;
}

export interface MarketOverviewRow {
  coin: string;
  name: string;
  price: number | null;
  change_24h_pct: number;
  last_bar_ts: number | null;
}

export interface Mover {
  coin: string;
  name: string;
  change_24h_pct: number;
}

export interface RecentAnalysisEntry {
  ts: number;
  coin: string;
  direction: Direction;
  text: string;
}

export interface ActivityEntry {
  ts: number;
  kind: string;
  coin: string | null;
  text: string;
}

/** JournalEntry aliases the activity-feed shape on the snapshot. */
export type JournalEntry = ActivityEntry;

/** The dashboard snapshot: one call renders the whole read-only board. */
export interface Snapshot {
  snapshot_event_id: string;
  ts: number;
  data_as_of: number | null;
  eligible_count: number | null;
  evaluated_count: number | null;
  active_setups: number;
  monitoring_coverage: number | null;
  new_setups_24h: number;
  setups: Setup[];
  comparison_24h: SnapshotComparison24h;
  snapshot_history: SnapshotHistoryRow[];
  coins_without_setup: CoinWithoutSetup[];
  market_overview: MarketOverviewRow[];
  movers: Mover[];
  recent_analysis: RecentAnalysisEntry[];
  activity: JournalEntry[];
  supported_timeframes: BarTimeframe[];
  event_kinds: string[];
  /** Section-level unavailability. null = everything answered. */
  unavailable: string | null;
}

/** One OHLCV bar. closed bars only (closed_only: true by contract). */
export interface Bar {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  closed: boolean;
}

export interface Bars {
  coin: string;
  tf: BarTimeframe;
  bars: Bar[];
  closed_only: boolean;
  count: number;
  data_as_of: number | null;
  /** null = the feed answered; a reason string = section-level unavailability. */
  unavailable: string | null;
}

/** Indicator metrics for a coin, with section-level unavailability. */
export interface Metrics {
  coin: string;
  ts: number;
  rsi: number | null;
  macd: number | null;
  ema50: number | null;
  ema200: number | null;
  volume_pct: number | null;
  unavailable: { reason: string; missing: string[] } | null;
  data_as_of: number;
}

export interface TradePlanEntryZone {
  low: number;
  high: number;
}

export interface TradePlanIndicator {
  name: string;
  value: number | null;
}

/** Reference-only trade plan. No leverage, no liquidation fields — by contract. */
export interface TradePlan {
  id: string;
  setup_id: string;
  coin: string;
  name: string;
  direction: Direction;
  horizon: string;
  entry_zone: TradePlanEntryZone;
  stop: number;
  tp1: number;
  tp2: number;
  reference_notional_usdt: number;
  reference_stop_risk_usdt: number;
  reference_size_note: string;
  indicators: TradePlanIndicator[];
  valid_until_ts: number;
  next_update_ts: number;
  analysis: string[];
  data_as_of: number;
}

/** One SSE transition event (also the shape the events stream emits). */
export interface Event {
  id: string;
  kind: string;
  ts: number;
  coin: string | null;
}

/** Shadow-watcher run state from GET /quant-status (Task 3 contract). */
export type QuantRunStatus = "unseen" | "healthy" | "paused" | "unavailable";

/** Cumulative shadow-event counts — counts only, never levels or advice. */
export interface QuantEventCounts {
  total: number;
  emitted: number;
  resolved: number;
}

/** One top-suppression row: rule + reason + occurrences. */
export interface QuantSuppressionRow {
  rule_id: string;
  reason: string;
  n: number;
}

/** Holdout interval behind a promotion record (endpoints only).
 *
 * The backend stores the whole holdout summary (research.summarize): the
 * sample counts, the day-cluster interval and the outcome shares. The
 * Research view reads ONLY `ci_lo` / `ci_hi` from it — a point reading of
 * the result is never rendered. */
export interface QuantHoldout {
  n: number;
  days: number;
  coins: number;
  mean_r: number;
  ci_lo: number;
  ci_hi: number;
  tp: number;
  sl: number;
  time: number;
  t: number;
}

/** A passed gate record — the only promotion shape the view renders. */
export interface QuantPromotion {
  rule_id: string;
  config_hash: string;
  passed: boolean;
  reasons: string[];
  holdout: QuantHoldout;
  expected_net_r: number;
}

/**
 * GET /quant-status body (proto/service.py _handle_quant_status).
 * 200 whenever the quant store is readable; `reason` carries
 * insufficient-evidence while event counts are zero. 503 UNAVAILABLE only
 * when the store itself is unreadable.
 */
export interface QuantStatus {
  mode: "shadow";
  status: QuantRunStatus;
  paused: boolean;
  pause_reason: string | null;
  config_hash: string | null;
  event_counts: QuantEventCounts;
  /** Legacy ACTIONABLE rows, counted separately — never mixed into shadow counts. */
  actionable_events: number;
  suppression_top: QuantSuppressionRow[];
  promotions: QuantPromotion[];
  reason: string | null;
}

/** Counts carried by a quant.shadow SSE frame (never levels or advice). */
export interface QuantShadowState {
  emitted: number;
  resolved: number;
}

/** A quant.shadow SSE frame: counts only, coin always null. */
export interface QuantEvent extends Event {
  kind: "quant.shadow";
  state?: QuantShadowState;
}

/* -------------------------------------------------------------------------
 * GET /quant-research — the read-only research surface (research-surface-v1)
 *
 * HARD RULES of this contract:
 *   * Uncertainty is INTERVAL ENDPOINTS ONLY. There is no mean, no point
 *     reading of the result, anywhere in this shape.
 *   * Every denominator is explicit; a value that cannot be computed is an
 *     explicit null (rendered "unavailable"), never a zero and never a
 *     guess.
 *   * A rule is `validated` only when a PASSED promotion record exists for
 *     it; a validated rule is still research, never an action.
 * ---------------------------------------------------------------------- */

/** Interval endpoints only — the pair, never a mean. */
export interface QuantResearchInterval {
  lo: number | null;
  hi: number | null;
}

/** The share of the resolved rows that ended TP / SL / TIME (or null). */
export interface QuantResearchOutcomeShares {
  tp: number | null;
  sl: number | null;
  time: number | null;
}

/** Whether a passed gate record exists for this rule id + config hash. */
export interface QuantResearchValidation {
  status: "not_validated" | "validated";
  /** The gate's own reasons — why it did not pass, or what it recorded. */
  reasons: string[];
}

/** One recorded suppression reason and how often it happened. */
export interface QuantResearchSuppression {
  reason: string;
  n: number;
}

/** One rule's row of the research surface. */
export interface QuantResearchRule {
  rule_id: string;
  role: "candidate" | "control" | "?";
  events: number;
  resolved: number;
  open: number;
  /**
   * Distinct days with a resolved outcome — the interval's denominator.
   * null when the rule has no resolved row (nothing to measure).
   */
  days: number | null;
  /**
   * Distinct coins with a resolved outcome. null when the rule has no
   * resolved row.
   */
  coins: number | null;
  /** The measured range's endpoints, or null when it cannot be computed. */
  interval: QuantResearchInterval | null;
  outcome_shares: QuantResearchOutcomeShares;
  /** What the costs are (an assumption, not a measurement). */
  cost_note: string;
  suppression: QuantResearchSuppression[];
  validation: QuantResearchValidation;
}

/** The sample the whole surface describes, with its denominators. */
export interface QuantResearchSample {
  events: number;
  resolved: number;
  open: number;
  days: number;
  coins: number;
  rules: number;
}

/** One measured feature/measure version behind the numbers. */
export interface QuantResearchFeatureVersion {
  name: string;
  version: string;
}

/** Where the numbers came from. */
export interface QuantResearchProvenance {
  config_hash: string | null;
  code_rev: string | null;
  /** The rule ids the registered config defines. */
  rule_ids: string[];
  feature_versions: QuantResearchFeatureVersion[];
}

/** One calibration bucket (a measured-outcome range and its rows). */
export interface QuantResearchCalibrationBucket {
  label: string;
  /** Rows in the bucket — the denominator. */
  n: number;
  /** The measured outcome share in the bucket, or null. */
  measured: number | null;
  /** The reference share the bucket is compared against, or null. */
  expected: number | null;
}

/** Calibration status: a table with denominators, or a reason. */
export interface QuantResearchCalibration {
  available: boolean;
  buckets: QuantResearchCalibrationBucket[];
  /** How many buckets the payload carries — the view never counts. */
  count: number;
  reason: string | null;
}

/** Coverage: attributed events over the recorded opportunities. */
export interface QuantResearchCoverage {
  attributed: number;
  eligible: number;
  ratio: number | null;
  reason: string | null;
}

/** The validation summary — the counts come from the API, never the view. */
export interface QuantResearchValidationSummary {
  /** Rules with a passed gate record. */
  validated: number;
  /** Rules the surface reports. */
  rules: number;
}

/**
 * GET /quant-research body (proto/quant/research_surface.py).
 * 200 whenever the quant store is readable. `status` is "insufficient"
 * when there are no shadow events yet, and `reasons` says why.
 */
export interface QuantResearch {
  version: string;
  status: "ok" | "insufficient";
  reasons: string[];
  /** UTC-seconds integer. */
  generated_ts: number;
  provenance: QuantResearchProvenance;
  sample: QuantResearchSample;
  rules: QuantResearchRule[];
  calibration: QuantResearchCalibration;
  coverage: QuantResearchCoverage;
  validation: QuantResearchValidationSummary;
}
