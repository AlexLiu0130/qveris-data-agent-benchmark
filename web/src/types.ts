/**
 * Mirror of the server-side public projection. Every field here was verified
 * against a live `arena_http` server, not against the handoff doc — where the
 * two disagree the code wins.
 *
 * Anything NOT in `_VARIANT_FIELDS` / `_SNAPSHOT_FIELDS` upstream is absent on
 * purpose. Adding a field the server does not whitelist does not make it
 * appear; it makes the server answer 500 `unsafe_projection`.
 */

export const SUITES = ["realtime_quote", "historical_price", "financial_statements"] as const;
export type SuiteId = (typeof SUITES)[number];

/** Server-derived. Never inferred client-side from execution counters. */
export type RunStatus = "queued" | "running" | "completed" | "incomplete" | "failed";

export type ProjectionStatus = "SCORED" | "SCORED_NOT_RANKED" | "UNSCORED";

/** `null` means the server declined to state a status, which is not the same as UNSCORED. */
export type ScoringStatus = "SCORED" | "UNSCORED" | null;

/** A cell is `queued` until a terminal event lands; there is no intermediate
 *  "running" state on the wire. */
export type CellState = "queued" | "success" | "failed" | "incomplete" | "blocked";

export interface Counters {
  total: number;
  completed: number;
  success: number;
  failed: number;
  incomplete: number;
  blocked: number;
}

export interface Cell {
  variant_id: string;
  case_id: string;
  trial: number;
  state: CellState;
}

export interface Scoring {
  semantic_accuracy: ScoringStatus;
  data_accuracy: ScoringStatus;
  end_to_end_latency: ScoringStatus;
  token_usage: ScoringStatus;
  coverage: ScoringStatus;
  rank: ScoringStatus;
  eligibility: ScoringStatus;
}

/** available / denominator ratios. Present only once a projection exists. */
export interface Ratio {
  available: number;
  denominator: number;
  value: number;
}

export interface PassRate {
  passed: number;
  denominator: number;
  value: number;
}

export interface DataAccuracy {
  passed_weight: number;
  eligible_weight: number;
  value: number;
}

export interface Latency {
  count: number;
  raw_count: number;
  p50_ms: number;
  p95_ms: number;
  max_ms: number;
  timeout_rate: number;
}

export interface TokenUsage {
  count: number;
  receipt_coverage: number;
  input_mean: number;
  input_p50: number;
  input_p95: number;
  output_mean: number;
  output_p50: number;
  output_p95: number;
  total_mean: number;
  total_p50: number;
  total_p95: number;
}

export interface Metrics {
  semantic_accuracy?: PassRate;
  data_accuracy?: DataAccuracy;
  end_to_end_latency?: Latency;
  token_usage?: TokenUsage;
}

export type Eligibility = "eligible" | "not_ranked" | "ineligible";

export interface Variant {
  variant_id: string;
  /** Card order. Fixed for the life of the run; progress must never reorder. */
  stable_display_order: number;
  suites: Record<SuiteId, Counters>;

  // Everything below appears only after the scorer has published a projection.
  metrics?: Metrics;
  case_pass_rate?: PassRate;
  semantic_oracle_coverage?: Ratio;
  oracle_coverage?: Ratio;
  receipt_coverage?: Ratio;
  completeness_reasons?: string[];
  eligibility?: Eligibility;
  ineligible_reason?: string;
  rank?: number;
}

export interface RunSummary {
  schema_version: string;
  run_id: string;
  manifest_hash: string;
  status: RunStatus;
  snapshot_sequence: number;
  event_cursor: number;
  updated_at: number;
  connection_basis: string;
  projection_status: ProjectionStatus;
  projection_reason: string;
  internal_status: string;
}

export interface RunSnapshot extends RunSummary {
  variants: Variant[];
  cells: Cell[];
  execution: Counters;
  scoring: Scoring;
}

export interface RunListResponse {
  schema_version: string;
  runs: RunSummary[];
}

export interface VariantDetailResponse {
  schema_version: string;
  run_id: string;
  variant: Variant;
}

/** Durable event names observed on the wire, plus the two stream control frames. */
export type StreamEventName =
  | "snapshot"
  | "resync_required"
  | "run_started"
  | "dispatch_intent"
  | "terminal"
  | "run_finished"
  | "scorer_projection";

/** A run in one of these states will never produce another durable event. */
export const TERMINAL_STATUSES: readonly RunStatus[] = ["completed", "incomplete", "failed"];

export function isTerminal(status: RunStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}
