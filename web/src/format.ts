import type {
  Counters,
  ProjectionStatus,
  RunSnapshot,
  RunStatus,
  ScoringStatus,
  SuiteId,
  Variant,
} from "./types";

/**
 * Display helpers. The single rule these all obey: an absent number is absent.
 *
 * Nothing here computes, derives, averages, or zero-fills a metric. If the
 * server did not publish a value, the UI says so. A "0%" that the scorer never
 * produced is a fabricated benchmark result, which is worse than a blank.
 */

export const NOT_AVAILABLE = "不可用";
export const UNKNOWN = "未知";

export function percent(value: number | undefined | null, digits = 1): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${(value * 100).toFixed(digits)}%`
    : NOT_AVAILABLE;
}

export function millis(value: number | undefined | null): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return NOT_AVAILABLE;
  return value >= 1000 ? `${(value / 1000).toFixed(2)} s` : `${value.toFixed(value < 10 ? 2 : 0)} ms`;
}

export function count(value: number | undefined | null): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumFractionDigits: 1 })
    : NOT_AVAILABLE;
}

export function fraction(part: number | undefined, whole: number | undefined): string {
  return typeof part === "number" && typeof whole === "number" ? `${part} / ${whole}` : NOT_AVAILABLE;
}

/**
 * Execution bookkeeping across suites. This is progress, not a metric: none of
 * these numbers is a pass rate, and the UI never labels them as one.
 */
export function sumCounters(suites: Partial<Record<SuiteId, Counters>>): Counters {
  const out: Counters = { total: 0, completed: 0, success: 0, failed: 0, incomplete: 0, blocked: 0 };
  for (const counters of Object.values(suites)) {
    if (!counters) continue;
    for (const key of Object.keys(out) as (keyof Counters)[]) out[key] += counters[key] ?? 0;
  }
  return out;
}

/** completed / total as a percentage string. Shown next to "Completed", never next to "Pass". */
export function progressPercent(part: number, whole: number): string {
  return whole > 0 ? `${((part / whole) * 100).toFixed(1)}%` : "0.0%";
}

export function clockUtc(epochSeconds: number | undefined): string {
  if (typeof epochSeconds !== "number" || !Number.isFinite(epochSeconds)) return UNKNOWN;
  return `${new Date(epochSeconds * 1000).toISOString().slice(11, 19)} UTC`;
}

export function relativeTime(epochSeconds: number | undefined, now = Date.now()): string {
  if (typeof epochSeconds !== "number" || !Number.isFinite(epochSeconds)) return UNKNOWN;
  const seconds = Math.max(0, Math.round(now / 1000 - epochSeconds));
  if (seconds < 5) return "刚刚";
  if (seconds < 60) return `${seconds} 秒前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)} 小时前`;
  return new Date(epochSeconds * 1000).toLocaleDateString("zh-CN");
}

export const RUN_STATUS_LABEL: Record<RunStatus, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "已完成",
  incomplete: "未完成",
  failed: "执行失败",
};

export const SUITE_LABEL = {
  realtime_quote: "实时行情",
  historical_price: "历史价格",
  financial_statements: "财务报表",
} as const;

export const METRIC_LABEL = {
  semantic_accuracy: "语义准确率",
  data_accuracy: "数据准确率",
  end_to_end_latency: "端到端延迟",
  token_usage: "Token 用量",
  coverage: "覆盖率",
  rank: "排名",
  eligibility: "准入资格",
} as const;

export function scoringLabel(status: ScoringStatus): string {
  if (status === "SCORED") return "已评分";
  if (status === "UNSCORED") return "未评分";
  return NOT_AVAILABLE;
}

/**
 * Splits variants for the leaderboard using only fields the server published.
 *
 * `ranked` requires an actual numeric `rank`. A variant the scorer chose not to
 * rank stays out of the ordered list rather than being appended at the bottom,
 * because appending would imply "last", which is a claim nobody made.
 */
export function partitionForLeaderboard(variants: readonly Variant[]): {
  ranked: Variant[];
  unranked: Variant[];
  ineligible: Variant[];
} {
  const ranked: Variant[] = [];
  const unranked: Variant[] = [];
  const ineligible: Variant[] = [];
  for (const variant of variants) {
    if (variant.eligibility === "ineligible") ineligible.push(variant);
    else if (typeof variant.rank === "number") ranked.push(variant);
    else unranked.push(variant);
  }
  ranked.sort((a, b) => (a.rank as number) - (b.rank as number));
  const byOrder = (a: Variant, b: Variant) => a.stable_display_order - b.stable_display_order;
  unranked.sort(byOrder);
  ineligible.sort(byOrder);
  return { ranked, unranked, ineligible };
}

/** Cards are ordered by the server's stable_display_order and never by progress. */
export function inDisplayOrder(variants: readonly Variant[]): Variant[] {
  return [...variants].sort((a, b) => a.stable_display_order - b.stable_display_order);
}

export function hasProjection(snapshot: Pick<RunSnapshot, "projection_status">): boolean {
  const status: ProjectionStatus = snapshot.projection_status;
  return status === "SCORED" || status === "SCORED_NOT_RANKED";
}
