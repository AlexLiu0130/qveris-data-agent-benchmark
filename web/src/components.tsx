import { useEffect, useRef } from "react";
import type { ConnectionState } from "./stream";
import {
  METRIC_LABEL,
  NOT_AVAILABLE,
  RUN_STATUS_LABEL,
  SUITE_LABEL,
  UNKNOWN,
  count,
  hasProjection,
  millis,
  partitionForLeaderboard,
  percent,
  progressPercent,
  relativeTime,
  scoringLabel,
  sumCounters,
} from "./format";
import {
  SUITES,
  type Cell,
  type Counters,
  type ProjectionStatus,
  type RunSnapshot,
  type RunStatus,
  type Variant,
} from "./types";
import closeIcon from "./assets/close.svg";
import failIcon from "./assets/fail.svg";
import haltedIcon from "./assets/halted.svg";
import incompleteIcon from "./assets/incomplete.svg";
import keyboardIcon from "./assets/keyboard.svg";
import lockIcon from "./assets/lock.svg";
import passIcon from "./assets/pass.svg";

/* Status is never communicated by color alone: every chip carries a glyph and a word. */

export type Tone = "pass" | "fail" | "warn" | "info" | "muted";

const CHIP_CLASS: Record<Tone, string> = {
  pass: "bg-[rgba(52,199,89,0.1)] border-[rgba(52,199,89,0.25)] text-[var(--color-pass)]",
  fail: "bg-[rgba(255,59,48,0.1)] border-[rgba(255,59,48,0.2)] text-[var(--color-fail)]",
  warn: "bg-[rgba(255,149,0,0.1)] border-[rgba(255,149,0,0.25)] text-[var(--color-incomplete)]",
  info: "bg-[rgba(0,113,227,0.08)] border-[rgba(0,113,227,0.2)] text-[var(--color-accent)]",
  muted: "bg-[rgba(224,223,228,0.7)] border-[var(--color-chip-3)] text-[#626267]",
};

const TEXT_CLASS: Record<Tone, string> = {
  pass: "text-[var(--color-pass)]",
  fail: "text-[var(--color-fail)]",
  warn: "text-[var(--color-incomplete)]",
  info: "text-[var(--color-accent)]",
  muted: "text-[var(--color-ink-3)]",
};

function Glyph({ shape }: { shape: Tone }) {
  const path = {
    pass: "M3 8.5l3.2 3.2L13 5",
    fail: "M4 4l8 8M12 4l-8 8",
    warn: "M8 3.5v6M8 12.2v.3",
    info: "M8 7.2v5M8 4v.3",
    muted: "M4 8h8",
  }[shape];
  return (
    <svg viewBox="0 0 16 16" className="h-2.5 w-2.5 shrink-0" aria-hidden="true" focusable="false">
      <path d={path} fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}

/** Pill chip from the design: 6px mark + 11px medium text, tinted background and border. */
export function Chip({
  tone,
  icon,
  children,
  className = "",
}: {
  tone: Tone;
  icon?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-[3px] text-[11px] font-medium leading-4 ${CHIP_CLASS[tone]} ${className}`}
    >
      {icon ? <img src={icon} alt="" className="h-[9.5px] w-[11px]" /> : <Glyph shape={tone} />}
      {children}
    </span>
  );
}

const RUN_TONE: Record<RunStatus, Tone> = {
  queued: "muted",
  running: "pass",
  completed: "info",
  incomplete: "warn",
  failed: "fail",
};

export function RunStatusChip({ status }: { status: RunStatus }) {
  return (
    <Chip tone={RUN_TONE[status]} icon={status === "failed" ? haltedIcon : undefined}>
      {RUN_STATUS_LABEL[status]}
    </Chip>
  );
}

const PROJECTION_TONE: Record<ProjectionStatus, Tone> = {
  SCORED: "pass",
  SCORED_NOT_RANKED: "warn",
  UNSCORED: "muted",
};

export function ProjectionChip({ snapshot }: { snapshot: RunSnapshot }) {
  return (
    <Chip tone={PROJECTION_TONE[snapshot.projection_status]}>
      投影状态 <span className="tnum">{snapshot.projection_status}</span>
    </Chip>
  );
}

const CONNECTION_COPY: Record<ConnectionState, { tone: Tone; label: string }> = {
  connecting: { tone: "muted", label: "连接中" },
  live: { tone: "pass", label: "实时" },
  reconnecting: { tone: "warn", label: "重连中" },
  stale: { tone: "warn", label: "数据滞后" },
  offline: { tone: "fail", label: "离线" },
  closed: { tone: "muted", label: "流已结束" },
};

/** Design's "LIVE · seq: n · 18ms" pill, with heartbeat copy replaced by connection state + last update. */
export function ConnectionPill({
  connection,
  attempt,
  sequence,
  receivedAt,
  now,
}: {
  connection: ConnectionState;
  attempt: number;
  sequence: number | undefined;
  receivedAt: number | null;
  now: number;
}) {
  const { tone, label } = CONNECTION_COPY[connection];
  const suffix = connection === "reconnecting" && attempt > 0 ? ` ${attempt}/5` : "";
  return (
    <div
      role="status"
      aria-live="polite"
      className="inline-flex min-h-11 items-center gap-1.5 rounded-full border border-[var(--color-hairline)] bg-[rgba(244,243,248,0.9)] px-3 py-1"
    >
      <span className={TEXT_CLASS[tone]}>
        <Glyph shape={tone} />
      </span>
      <span className="text-[11px] font-semibold uppercase tracking-[0.05em]">
        {label}
        {suffix}
      </span>
      <span className="h-1 w-1 rounded-full bg-black/15" aria-hidden="true" />
      <span className="tnum text-[11px] text-[var(--color-ink-3)]">
        seq: {typeof sequence === "number" ? sequence.toLocaleString("en-US") : UNKNOWN}
      </span>
      <span
        className={`rounded px-1.5 text-[11px] font-medium ${tone === "pass" ? "bg-[#e8f8ee] text-[var(--color-pass)]" : "bg-[var(--color-chip)] text-[var(--color-ink-2)]"}`}
      >
        {receivedAt === null ? UNKNOWN : relativeTime(receivedAt / 1000, now)}
      </span>
    </div>
  );
}

/* --------------------------------------------------------------- shared --- */

export function ProgressBar({
  value,
  max,
  label,
  className = "",
}: {
  value: number;
  max: number;
  label: string;
  className?: string;
}) {
  const width = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      className={`h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-track)] ${className}`}
    >
      <div
        className="h-full rounded-full bg-[var(--color-accent)] shadow-[0_0_8px_rgba(0,113,227,0.3)] transition-[width] duration-300"
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

const TILE_CLASS: Record<Tone, string> = {
  pass: "bg-[rgba(16,185,129,0.04)] border-[rgba(16,185,129,0.15)]",
  fail: "bg-[rgba(244,63,94,0.04)] border-[rgba(244,63,94,0.15)]",
  warn: "bg-[rgba(245,158,11,0.04)] border-[rgba(245,158,11,0.15)]",
  info: "bg-[rgba(0,113,227,0.04)] border-[rgba(0,113,227,0.15)]",
  muted: "bg-[var(--color-fill)] border-[rgba(0,0,0,0.06)]",
};

function CountTile({
  tone,
  icon,
  label,
  value,
}: {
  tone: Tone;
  icon?: string;
  label: string;
  value: number;
}) {
  return (
    <div className={`min-w-0 rounded-[var(--radius-control)] border p-2.5 ${TILE_CLASS[tone]}`}>
      <dt className={`flex items-center gap-1 text-[11px] font-medium ${TEXT_CLASS[tone]}`}>
        {icon ? <img src={icon} alt="" className="h-[10.8px] w-[10.8px]" /> : <Glyph shape={tone} />}
        <span className="truncate">{label}</span>
      </dt>
      <dd className="tnum mt-1 text-sm font-medium">{value}</dd>
    </div>
  );
}

/** Pass / Fail / Incom. tiles from the design; a Blocked tile appears only when the server reports one. */
function OutcomeTiles({ counters }: { counters: Counters }) {
  const blocked = counters.blocked > 0;
  return (
    <dl className={`grid gap-2 ${blocked ? "grid-cols-4" : "grid-cols-3"}`}>
      <CountTile tone="pass" icon={passIcon} label="通过" value={counters.success} />
      <CountTile tone="fail" icon={failIcon} label="失败" value={counters.failed} />
      <CountTile tone="warn" icon={incompleteIcon} label="未完成" value={counters.incomplete} />
      {blocked && <CountTile tone="muted" label="阻塞" value={counters.blocked} />}
    </dl>
  );
}

function SectionLabel({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-[13px] font-semibold">{children}</h3>
      {aside && <span className="text-[11px] font-medium text-[var(--color-ink-3)]">{aside}</span>}
    </div>
  );
}

/* ---------------------------------------------------------------- lanes --- */

export function LaneCard({
  variant,
  status,
  selected,
  onOpen,
}: {
  variant: Variant;
  status: RunStatus;
  selected: boolean;
  onOpen: () => void;
}) {
  const totals = sumCounters(variant.suites);
  const order = String(variant.stable_display_order).padStart(2, "0");
  const titleId = `lane-${variant.variant_id}`;

  return (
    <article
      aria-labelledby={titleId}
      className={`relative min-w-0 rounded-[var(--radius-card)] border bg-[var(--color-surface)] p-5 transition-shadow focus-within:shadow-[0_0_0_2px_rgba(0,113,227,0.35)] ${
        selected
          ? "border-[rgba(0,113,227,0.4)] shadow-[0_0_0_2px_rgba(0,113,227,0.2),0_4px_6px_-1px_rgba(0,0,0,0.1),0_2px_4px_-2px_rgba(0,0,0,0.1)]"
          : "border-[rgba(0,0,0,0.06)] shadow-[0_1px_1px_rgba(0,0,0,0.05)] hover:shadow-[0_4px_6px_-1px_rgba(0,0,0,0.08)]"
      }`}
    >
      {/* The whole card is the target (≥44px); text underneath stays readable by AT via aria-labelledby. */}
      <button
        type="button"
        onClick={onOpen}
        aria-label={`查看 ${variant.variant_id}`}
        aria-pressed={selected}
        className="absolute inset-0 z-[1] rounded-[var(--radius-card)] focus-visible:outline-none"
      />

      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-baseline gap-2">
            <span className="tnum text-xs font-medium text-[var(--color-ink-3)]">#{order}</span>
            <h3 id={titleId} className="truncate text-[17px] font-semibold tracking-[-0.025em]">
              {variant.variant_id}
            </h3>
          </div>
          {/* model_id / get_version are not in the public projection: reported as unknown, never invented. */}
          <p className="mt-0.5 flex items-center gap-2 text-[11px] font-medium text-[var(--color-ink-2)]">
            <span className="truncate">模型 {UNKNOWN}</span>
            <span className="h-1 w-0.5 rounded-full bg-black/15" aria-hidden="true" />
            <span className="tnum text-xs font-normal text-[var(--color-ink-3)]">get {UNKNOWN}</span>
          </p>
        </div>
        <RunStatusChip status={status} />
      </header>

      <div className="mt-3 flex items-end justify-between gap-3">
        <span className="text-[11px] font-medium leading-tight text-[var(--color-ink-2)]">
          已完成用例
        </span>
        <span className="tnum text-sm font-medium">
          {totals.completed} / {totals.total}{" "}
          <span className="text-[var(--color-ink-3)]">({progressPercent(totals.completed, totals.total)})</span>
        </span>
      </div>
      <ProgressBar
        value={totals.completed}
        max={totals.total}
        label={`${variant.variant_id} 已完成用例`}
        className="mt-1.5"
      />

      <div className="mt-3">
        <OutcomeTiles counters={totals} />
      </div>

      <footer className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-[var(--color-hairline)] pt-2.5 text-[11px]">
        {status === "failed" ? (
          <>
            <span className="font-medium text-[var(--color-ink-3)]">诊断状态</span>
            <span className="font-medium text-[var(--color-fail)]">
              Runner 异常中断，保持原固定位展示
            </span>
          </>
        ) : (
          <>
            {/* ponytail: the design's "Active Suite" is not published; per-suite progress is. */}
            <span className="font-medium text-[var(--color-ink-3)]">套件进度</span>
            {SUITES.map((suite) => {
              const c = variant.suites[suite];
              return (
                <span
                  key={suite}
                  className="tnum rounded bg-[var(--color-chip)] px-1.5 py-0.5 text-xs font-medium text-[var(--color-ink-4)]"
                >
                  {suite} {c ? `${c.completed}/${c.total}` : NOT_AVAILABLE}
                </span>
              );
            })}
          </>
        )}
      </footer>
    </article>
  );
}

/* ---------------------------------------------------------- leaderboard --- */

function DeferredLeaderboard({ snapshot }: { snapshot: RunSnapshot }) {
  return (
    <div className="rounded-[var(--radius-card)] border border-dashed border-[var(--color-hairline)] bg-[var(--color-surface)] p-8 text-center">
      <Chip tone="muted">未评分</Chip>
      <p className="mt-3 text-sm text-[var(--color-ink-2)]">
        评分器尚未为该 run 发布投影，暂无榜单可展示。
      </p>
      <p className="tnum mt-2 text-xs text-[var(--color-ink-3)]">
        {snapshot.projection_status} · {snapshot.projection_reason}
      </p>
      <p className="mx-auto mt-3 max-w-md text-xs text-[var(--color-ink-3)]">
        此处刻意不展示执行计数：它们不是通过率，拿来充当通过率会误报评测结果。
      </p>
    </div>
  );
}

const ELIGIBILITY_TONE: Record<NonNullable<Variant["eligibility"]>, Tone> = {
  eligible: "pass",
  not_ranked: "muted",
  ineligible: "warn",
};

function EligibilityChip({ variant }: { variant: Variant }) {
  if (!variant.eligibility) return <Chip tone="muted">准入状态 {NOT_AVAILABLE}</Chip>;
  return <Chip tone={ELIGIBILITY_TONE[variant.eligibility]}>{ELIGIBILITY_LABEL[variant.eligibility]}</Chip>;
}

const ELIGIBILITY_LABEL: Record<NonNullable<Variant["eligibility"]>, string> = {
  eligible: "已准入",
  not_ranked: "未排名",
  ineligible: "不符合准入",
};

const TH = "px-4 py-3 text-left text-[11px] font-medium uppercase tracking-[0.04em] text-[var(--color-ink-2)]";

export function Leaderboard({
  snapshot,
  onOpen,
}: {
  snapshot: RunSnapshot;
  onOpen: (variant: Variant) => void;
}) {
  if (!hasProjection(snapshot)) return <DeferredLeaderboard snapshot={snapshot} />;

  const { ranked, unranked, ineligible } = partitionForLeaderboard(snapshot.variants);

  return (
    <section aria-labelledby="leaderboard-title">
      <div className="flex flex-wrap items-center justify-between gap-2 pb-4">
        <div className="flex items-center gap-2">
          <h2 id="leaderboard-title" className="text-[17px] font-semibold tracking-[-0.025em]">
            正式参评候选体
          </h2>
          <span className="tnum rounded-full bg-[var(--color-chip)] px-2 py-0.5 text-xs font-medium text-[var(--color-ink-4)]">
            {ranked.length} 款入榜
          </span>
        </div>
        <p className="text-[11px] font-medium text-[var(--color-ink-2)]">
          排名与指标均由评分器发布 · 点击行查看深度套件剖析
        </p>
      </div>

      <div className="overflow-x-auto rounded-[var(--radius-card)] border border-[var(--color-hairline)] bg-[var(--color-surface)] shadow-[0_1px_2px_rgba(0,0,0,0.05)]">
        <table className="w-full min-w-[64rem] border-collapse text-sm">
          <caption className="sr-only">
            已排名选手。顺序由评分器发布，浏览器不做计算。
          </caption>
          <thead>
            <tr className="border-b border-[var(--color-hairline)] bg-[var(--color-canvas)]">
              <th scope="col" className={`${TH} w-[4.5rem] text-center`}>排名</th>
              <th scope="col" className={`${TH} min-w-[16rem]`}>选手 / 模型</th>
              <th scope="col" className={`${TH} text-center`}>用例通过率</th>
              <th scope="col" className={`${TH} text-center`}>数据准确率</th>
              <th scope="col" className={`${TH} text-center`}>语义准确率</th>
              <th scope="col" className={`${TH} text-center`}>P95 延迟</th>
              <th scope="col" className={`${TH} text-right`}>平均 Total Token</th>
              <th scope="col" className={`${TH} min-w-[13rem]`}>覆盖率</th>
              <th scope="col" className={`${TH} text-center`}>准入状态</th>
            </tr>
          </thead>
          <tbody>
            {ranked.map((variant) => {
              const first = variant.rank === 1;
              const m = variant.metrics;
              return (
                <tr
                  key={variant.variant_id}
                  className={`relative border-t border-[var(--color-hairline)] first:border-t-0 ${first ? "bg-[rgba(215,226,255,0.2)]" : ""}`}
                >
                  <td className="px-3 py-6 text-center">
                    {first && (
                      <span aria-hidden="true" className="absolute inset-y-0 left-0 w-1 rounded-r bg-[var(--color-accent)]" />
                    )}
                    <span
                      className={`tnum inline-flex h-7 w-7 items-center justify-center rounded-full text-sm font-bold ${
                        first ? "bg-[var(--color-accent)] text-white" : "bg-[var(--color-chip-3)]"
                      }`}
                    >
                      {variant.rank}
                    </span>
                  </td>
                  <th scope="row" className="px-4 py-5 text-left font-normal">
                    <button
                      type="button"
                      onClick={() => onOpen(variant)}
                      className={`tnum block min-h-11 text-[17px] font-semibold tracking-[-0.025em] hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)] ${first ? "text-[var(--color-accent)]" : ""}`}
                    >
                      {variant.variant_id}
                      <span className="ml-2 rounded bg-[var(--color-chip)] px-1.5 py-0.5 text-xs font-normal text-[var(--color-ink-4)]">
                        get {UNKNOWN}
                      </span>
                    </button>
                    <p className="mt-1 text-[11px] font-medium text-[var(--color-ink-2)]">
                      模型 {UNKNOWN}
                    </p>
                  </th>
                  <td className="tnum px-4 py-5 text-center">
                    <span className="block text-2xl font-bold tracking-[-0.025em]">
                      {percent(variant.case_pass_rate?.value, 2)}
                    </span>
                    <span className="block text-xs text-[var(--color-ink-3)]">
                      {variant.case_pass_rate
                        ? `${variant.case_pass_rate.passed} / ${variant.case_pass_rate.denominator}`
                        : NOT_AVAILABLE}
                    </span>
                  </td>
                  <td className="tnum px-4 py-5 text-center">{percent(m?.data_accuracy?.value)}</td>
                  <td className="tnum px-4 py-5 text-center">{percent(m?.semantic_accuracy?.value)}</td>
                  <td className="tnum px-4 py-5 text-center">{millis(m?.end_to_end_latency?.p95_ms)}</td>
                  <td className="tnum px-4 py-5 text-right">{count(m?.token_usage?.total_mean)}</td>
                  <td className="px-4 py-5">
                    <div className="flex items-center justify-between gap-2 text-[11px]">
                      <span className="font-medium text-[var(--color-ink-2)]">Oracle/Receipt</span>
                      <span className="tnum whitespace-nowrap">
                        {percent(variant.oracle_coverage?.value, 1)} / {percent(variant.receipt_coverage?.value, 1)}
                      </span>
                    </div>
                    {variant.oracle_coverage && (
                      <div
                        role="progressbar"
                        aria-label={`${variant.variant_id} Oracle 覆盖率`}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-valuenow={Math.round(variant.oracle_coverage.value * 100)}
                        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-chip)]"
                      >
                        <div
                          className="h-full rounded-full bg-[var(--color-pass)]"
                          style={{ width: `${Math.min(100, variant.oracle_coverage.value * 100)}%` }}
                        />
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-5 text-center">
                    <EligibilityChip variant={variant} />
                  </td>
                </tr>
              );
            })}
            {ranked.length === 0 && (
              <tr>
                <td colSpan={9} className="px-4 py-6 text-center text-sm text-[var(--color-ink-2)]">
                  评分器已生成投影，但未对任何选手排名。
                </td>
              </tr>
            )}
          </tbody>
        </table>

        {(unranked.length > 0 || ineligible.length > 0) && (
          <div className="space-y-2 border-t border-[var(--color-hairline)] bg-[var(--color-fill)] px-4 py-3">
            {[...unranked, ...ineligible].map((variant) => (
              <p key={variant.variant_id} className="flex flex-wrap items-center gap-2 text-xs">
                <EligibilityChip variant={variant} />
                <button
                  type="button"
                  onClick={() => onOpen(variant)}
                  className="tnum min-h-11 font-medium hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
                >
                  {variant.variant_id}
                </button>
                <span className="text-[var(--color-ink-3)]">
                  {variant.ineligible_reason || variant.completeness_reasons?.join(", ") || NOT_AVAILABLE}
                </span>
              </p>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- sheet --- */

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="tnum rounded border border-[var(--color-hairline)] bg-white px-[5px] py-[2.5px] text-[10px] font-medium leading-none text-[var(--color-ink)]">
      {children}
    </kbd>
  );
}

/** Big-number tile from "Formal Projected Telemetry". Absent values render as text, not as zero. */
function MetricTile({
  label,
  value,
  unit,
  accent = false,
  sub,
}: {
  label: string;
  value: string;
  unit?: string;
  accent?: boolean;
  sub?: string;
}) {
  const available = value !== NOT_AVAILABLE;
  return (
    <div className="min-w-0 rounded-[var(--radius-card)] border border-[rgba(0,0,0,0.03)] bg-[var(--color-fill)] p-3.5">
      <p className="truncate text-[11px] font-medium text-[var(--color-ink-3)]">{label}</p>
      <p
        className={`tnum mt-1 ${
          available
            ? `text-2xl font-medium tracking-[-0.025em] ${accent ? "text-[var(--color-accent)]" : ""}`
            : "text-sm text-[var(--color-ink-3)]"
        }`}
      >
        {value}
        {available && unit && (
          <span className="ml-0.5 text-sm font-normal text-[var(--color-ink-2)]">{unit}</span>
        )}
      </p>
      {sub && <p className="tnum mt-0.5 text-xs text-[var(--color-ink-3)]">{sub}</p>}
    </div>
  );
}

function SuiteBreakdown({ variant }: { variant: Variant }) {
  return (
    <ul className="mt-3 space-y-2">
      {SUITES.map((suite) => {
        const c = variant.suites[suite];
        return (
          <li
            key={suite}
            className="rounded-[var(--radius-card)] border border-[rgba(0,0,0,0.03)] bg-[var(--color-fill)] p-3.5"
          >
            <div className="flex items-center justify-between gap-3">
              <span className="truncate text-[13px] font-medium">
                {SUITE_LABEL[suite]} <span className="tnum text-[var(--color-ink-3)]">({suite})</span>
              </span>
              <span className="tnum shrink-0 whitespace-nowrap text-sm font-medium">
                {c ? `${c.completed} / ${c.total}` : NOT_AVAILABLE}
              </span>
            </div>
            {c && (
              <>
                <ProgressBar value={c.completed} max={c.total} label={`${suite} 已完成`} className="mt-2" />
                <p className="mt-2 flex flex-wrap gap-x-3 text-[11px] font-medium">
                  <span className="text-[var(--color-pass)]">通过: {c.success}</span>
                  <span className="text-[var(--color-fail)]">失败: {c.failed}</span>
                  <span className="text-[var(--color-incomplete)]">未完成: {c.incomplete}</span>
                  {c.blocked > 0 && <span className="text-[var(--color-ink-3)]">阻塞: {c.blocked}</span>}
                </p>
              </>
            )}
          </li>
        );
      })}
    </ul>
  );
}

const CELL_TONE: Record<Cell["state"], string> = {
  success: "bg-[var(--color-pass)] text-white",
  failed: "bg-[var(--color-fail)] text-white",
  incomplete: "bg-[var(--color-incomplete)] text-white",
  blocked: "bg-[var(--color-ink-3)] text-white",
  queued: "bg-[var(--color-fill)] text-[var(--color-ink-3)] ring-1 ring-inset ring-[var(--color-hairline)]",
};

const CELL_MARK: Record<Cell["state"], string> = {
  success: "✓",
  failed: "✕",
  incomplete: "!",
  blocked: "–",
  queued: "·",
};

function CellStrip({ cells }: { cells: Cell[] }) {
  if (cells.length === 0) {
    return <p className="mt-3 text-xs text-[var(--color-ink-3)]">尚无用例结果。</p>;
  }
  return (
    <ul className="mt-3 flex flex-wrap gap-1" aria-label="用例结果">
      {cells.map((cell) => (
        <li
          key={`${cell.case_id}#${cell.trial}`}
          title={`${cell.case_id} · 第 ${cell.trial} 次 · ${cell.state}`}
          className={`flex h-5 w-5 items-center justify-center rounded-[3px] text-[9px] font-bold ${CELL_TONE[cell.state]}`}
        >
          <span aria-hidden="true">{CELL_MARK[cell.state]}</span>
          <span className="sr-only">{`${cell.case_id} 第 ${cell.trial} 次: ${cell.state}`}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * Native <dialog showModal> rather than a hand-rolled focus trap: it moves
 * focus in, makes the rest of the page inert, and closes on Escape for free.
 */
export function VariantSheet({
  variant,
  snapshot,
  cells,
  onClose,
}: {
  variant: Variant;
  snapshot: RunSnapshot;
  cells: Cell[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    // showModal() is what buys the focus trap, the inert background and the
    // implicit aria-modal. Its `close`/`cancel` events, however, are not
    // dispatched by every embedded webview, so dismissal is wired to keydown
    // and routed through React state instead of through the dialog's own event.
    // Unmounting the dialog races its own focus restoration, so remember the
    // control that opened the sheet and hand focus back explicitly on close.
    const invoker = document.activeElement as HTMLElement | null;
    if (!dialog.open) dialog.showModal();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onClose();
    };
    dialog.addEventListener("keydown", onKeyDown);
    return () => {
      dialog.removeEventListener("keydown", onKeyDown);
      if (dialog.open) dialog.close();
      if (invoker?.isConnected) invoker.focus();
    };
  }, [onClose]);

  const scored = hasProjection(snapshot);
  const m = variant.metrics;
  const activeSuites = SUITES.filter((s) => (variant.suites[s]?.total ?? 0) > 0).length;

  return (
    <dialog
      ref={ref}
      aria-labelledby="sheet-title"
      className="sheet fixed inset-y-0 right-0 left-auto m-0 flex h-auto max-h-none w-full max-w-[440px] flex-col bg-[var(--color-surface)] p-0 text-[var(--color-ink)] shadow-[0_16px_18px_rgba(0,0,0,0.06)] backdrop:bg-black/30 sm:inset-y-3 sm:right-3 sm:rounded-2xl sm:border sm:border-[var(--color-hairline)]"
    >
      <header className="border-b border-[var(--color-hairline)] px-6 pb-4 pt-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="rounded bg-[var(--color-chip-3)] px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.05em] text-[#626267]">
                正在查看
              </span>
              <RunStatusChip status={snapshot.status} />
            </div>
            <h2 id="sheet-title" className="tnum mt-1.5 truncate text-[22px] font-semibold tracking-[-0.015em]">
              {variant.variant_id}
            </h2>
            <p className="mt-0.5 truncate text-[13px] text-[var(--color-ink-2)]">
              模型 {UNKNOWN} · get {UNKNOWN} · <span className="tnum">{snapshot.run_id}</span>
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="-mr-2 -mt-1 flex h-11 w-11 shrink-0 items-center justify-center rounded-[var(--radius-control)] hover:bg-[var(--color-fill)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
          >
            <img src={closeIcon} alt="" className="h-[11.67px] w-[11.67px]" />
            <span className="sr-only">关闭选手详情</span>
          </button>
        </div>

        <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-[var(--radius-control)] border border-[rgba(0,0,0,0.03)] bg-[var(--color-fill)] px-3 py-2 text-[11px] font-medium text-[var(--color-ink-3)]">
          <span className="flex items-center gap-1.5">
            <img src={keyboardIcon} alt="" className="h-[8.75px] w-[12.5px]" />
            快捷键
          </span>
          <span className="flex items-center gap-1.5">
            切换选手 <Kbd>↑</Kbd> <Kbd>↓</Kbd> / <Kbd>J</Kbd> <Kbd>K</Kbd>
            <span className="text-black/15" aria-hidden="true">•</span>
            关闭 <Kbd>Esc</Kbd>
          </span>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <section>
          <SectionLabel aside={`${activeSuites} 个活跃套件`}>评测套件明细</SectionLabel>
          <SuiteBreakdown variant={variant} />
        </section>

        <section className="mt-6">
          <SectionLabel aside={scored ? snapshot.projection_status : "未评分"}>正式投影指标</SectionLabel>
          {!scored && (
            <p className="tnum mt-2 text-xs text-[var(--color-ink-3)]">
              {snapshot.projection_status}: {snapshot.projection_reason}。数值保持空白，不以 0 填充。
            </p>
          )}
          <div className="mt-3 grid grid-cols-2 gap-2">
            <MetricTile
              label="用例通过率"
              value={percent(variant.case_pass_rate?.value, 2)}
              accent
              sub={
                variant.case_pass_rate
                  ? `${variant.case_pass_rate.passed} / ${variant.case_pass_rate.denominator}`
                  : undefined
              }
            />
            <MetricTile
              label="排名"
              value={typeof variant.rank === "number" ? `#${variant.rank}` : NOT_AVAILABLE}
              sub={variant.eligibility ? ELIGIBILITY_LABEL[variant.eligibility] : undefined}
            />
            <MetricTile label={METRIC_LABEL.semantic_accuracy} value={percent(m?.semantic_accuracy?.value)} />
            <MetricTile label={METRIC_LABEL.data_accuracy} value={percent(m?.data_accuracy?.value)} />
            <MetricTile label="端到端 P95" value={millis(m?.end_to_end_latency?.p95_ms)} />
            <MetricTile label="平均 Total Token" value={count(m?.token_usage?.total_mean)} unit="tok" />
          </div>
        </section>

        <section className="mt-6 rounded-[var(--radius-card)] border border-[rgba(0,0,0,0.04)] bg-[rgba(233,231,237,0.5)] p-3.5">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-[11px] font-semibold uppercase tracking-[0.05em] text-[var(--color-ink-3)]">
              准入门控
            </h3>
            <EligibilityChip variant={variant} />
          </div>
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13px]">
            {(
              [
                ["Oracle 覆盖率", variant.oracle_coverage?.value],
                ["语义 Oracle 覆盖率", variant.semantic_oracle_coverage?.value],
                ["Receipt 覆盖率", variant.receipt_coverage?.value],
                ["超时率", m?.end_to_end_latency?.timeout_rate],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="contents">
                <dt className="text-[var(--color-ink-2)]">{label}:</dt>
                <dd className="tnum text-right text-xs font-medium leading-5">{percent(value)}</dd>
              </div>
            ))}
          </dl>
          {variant.ineligible_reason && (
            <p className="mt-2 text-xs text-[var(--color-incomplete)]">{variant.ineligible_reason}</p>
          )}
          {variant.completeness_reasons && variant.completeness_reasons.length > 0 && (
            <ul className="mt-2 list-disc pl-4 text-xs text-[var(--color-ink-2)]">
              {variant.completeness_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          )}
        </section>

        <section className="mt-6">
          <SectionLabel aside="状态由服务端给出">用例格</SectionLabel>
          <CellStrip cells={cells} />
        </section>

        <section className="mt-6">
          <SectionLabel>评分状态</SectionLabel>
          <dl className="mt-2 grid grid-cols-[1fr_auto] gap-y-1 text-xs">
            {(Object.keys(METRIC_LABEL) as (keyof typeof METRIC_LABEL)[]).map((key) => (
              <div key={key} className="contents">
                <dt className="text-[var(--color-ink-2)]">{METRIC_LABEL[key]}</dt>
                <dd className="tnum">{scoringLabel(snapshot.scoring[key])}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>

      <footer className="flex items-center justify-between gap-3 border-t border-[var(--color-hairline)] px-6 py-3 text-[11px] font-medium text-[var(--color-ink-3)]">
        <span className="flex items-center gap-1.5">
          <img src={lockIcon} alt="" className="h-[11.4px] w-[8.7px]" />
          只读投影
        </span>
        <span className="flex items-center gap-1.5">
          按 <kbd className="tnum rounded bg-[var(--color-chip-2)] px-1.5 py-0.5 text-[10px] text-[var(--color-ink)]">Esc</kbd> 关闭
        </span>
      </footer>
    </dialog>
  );
}

export function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--color-hairline)] bg-[var(--color-surface)] p-8 text-center">
      <h2 className="text-sm font-semibold">{title}</h2>
      <div className="mt-2 text-sm text-[var(--color-ink-2)]">{children}</div>
    </section>
  );
}
