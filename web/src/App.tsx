import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ConnectionPill,
  LaneCard,
  Leaderboard,
  Panel,
  ProjectionChip,
  VariantSheet,
} from "./components";
import { RUN_STATUS_LABEL, UNKNOWN, clockUtc, inDisplayOrder } from "./format";
import { useRunStream, useRuns, useTicker } from "./useRun";
import type { Cell, Variant } from "./types";
import chevronIcon from "./assets/chevron.svg";
import logoIcon from "./assets/logo.svg";

type Tab = "lanes" | "leaderboard";

const RUN_DOT: Record<string, string> = {
  queued: "bg-[var(--color-ink-3)]",
  running: "bg-[var(--color-pass)]",
  completed: "bg-[var(--color-accent)]",
  incomplete: "bg-[var(--color-incomplete)]",
  failed: "bg-[var(--color-fail)]",
};

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex items-center gap-2">
      <span className="text-[11px] font-medium uppercase tracking-[0.04em] text-[var(--color-ink-3)]">
        {label}
      </span>
      <span className="tnum text-xs">{value}</span>
    </span>
  );
}

const BANNER: Record<"fail" | "warn", string> = {
  fail: "border-[var(--color-fail)]/30 bg-[color-mix(in_srgb,var(--color-fail)_8%,transparent)]",
  warn: "border-[var(--color-incomplete)]/30 bg-[color-mix(in_srgb,var(--color-incomplete)_10%,transparent)]",
};

function Banner({ tone, children }: { tone: "fail" | "warn"; children: React.ReactNode }) {
  return (
    <p className={`mb-4 rounded-[var(--radius-card)] border px-4 py-2 text-xs text-[var(--color-ink-2)] ${BANNER[tone]}`}>
      {children}
    </p>
  );
}

export default function App() {
  const runs = useRuns();
  const [runId, setRunId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("lanes");
  const [openVariant, setOpenVariant] = useState<string | null>(null);
  const now = useTicker();
  const closeSheet = useCallback(() => setOpenVariant(null), []);

  useEffect(() => {
    if (runId === null && runs.data && runs.data.length > 0) setRunId(runs.data[0].run_id);
  }, [runId, runs.data]);

  const { snapshot, connection, attempt, error, loading, receivedAt } = useRunStream(runId);

  const lanes = useMemo(() => (snapshot ? inDisplayOrder(snapshot.variants) : []), [snapshot]);
  const cellsByVariant = useMemo(() => {
    const map = new Map<string, Cell[]>();
    for (const cell of snapshot?.cells ?? []) {
      const bucket = map.get(cell.variant_id);
      if (bucket) bucket.push(cell);
      else map.set(cell.variant_id, [cell]);
    }
    return map;
  }, [snapshot]);

  const selected: Variant | null =
    (openVariant && snapshot?.variants.find((v) => v.variant_id === openVariant)) || null;

  // ↑/↓ and J/K walk the lanes in stable_display_order while the sheet is open.
  useEffect(() => {
    if (!selected || lanes.length === 0) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const step = { ArrowDown: 1, j: 1, J: 1, ArrowUp: -1, k: -1, K: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const index = lanes.findIndex((v) => v.variant_id === selected.variant_id);
      const next = lanes[(index + step + lanes.length) % lanes.length];
      setOpenVariant(next.variant_id);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selected, lanes]);

  const selectedRun = runs.data?.find((r) => r.run_id === runId);
  const status = snapshot?.status ?? selectedRun?.status;

  return (
    <div className="min-h-dvh bg-[var(--color-canvas)]">
      <header className="sticky top-0 z-10 border-b border-[var(--color-hairline)] bg-[rgba(255,255,255,0.88)] shadow-[0_1px_3px_rgba(0,0,0,0.02)] backdrop-blur-[20px]">
        <div className="mx-auto flex max-w-[1280px] flex-wrap items-center justify-between gap-x-4 gap-y-2 px-6 py-1.5">
          <div className="flex items-center gap-3">
            <span className="flex h-7 w-7 items-center justify-center rounded-[var(--radius-control)] bg-[var(--color-logo)] shadow-[0_1px_1px_rgba(0,0,0,0.05)]">
              <img src={logoIcon} alt="" className="h-[15.75px] w-[16.5px]" />
            </span>
            <h1 className="text-[17px] font-semibold tracking-[-0.025em]">QVeris Arena</h1>
            <span className="tnum rounded-full bg-[rgba(224,223,228,0.8)] px-2 py-0.5 text-xs text-[#626267]">
              {runs.data?.[0]?.schema_version ?? snapshot?.schema_version ?? `schema ${UNKNOWN}`}
            </span>
          </div>

          {/* Run switcher: native <select> keeps keyboard and screen-reader behaviour for free. */}
          <label className="flex min-h-11 items-center gap-2 rounded-[var(--radius-control)] border border-black/40 bg-[var(--color-surface)] px-3 shadow-[0_1px_1px_rgba(0,0,0,0.04)]">
            <span className="sr-only">选择 run</span>
            <span
              aria-hidden="true"
              className={`h-2 w-2 shrink-0 rounded-full ${status ? RUN_DOT[status] : "bg-[var(--color-ink-3)]"}`}
            />
            <select
              value={runId ?? ""}
              onChange={(event) => {
                setRunId(event.target.value || null);
                setOpenVariant(null);
              }}
              disabled={!runs.data || runs.data.length === 0}
              className="tnum min-h-11 appearance-none bg-transparent text-center text-[13px] font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)]"
            >
              {(runs.data ?? []).map((run) => (
                <option key={run.run_id} value={run.run_id}>
                  {run.run_id} · {RUN_STATUS_LABEL[run.status]}
                </option>
              ))}
              {(!runs.data || runs.data.length === 0) && <option value="">暂无 run</option>}
            </select>
            <span className="tnum w-14 text-center text-[11px] font-medium leading-[14px] text-[var(--color-ink-3)]">
              {snapshot ? clockUtc(snapshot.updated_at).replace(" ", "\n") : UNKNOWN}
            </span>
            <img src={chevronIcon} alt="" className="h-[11.93px] w-1.5" />
          </label>

          <div className="flex flex-wrap items-center gap-3">
            <ConnectionPill
              connection={connection}
              attempt={attempt}
              sequence={snapshot?.snapshot_sequence}
              receivedAt={receivedAt}
              now={now}
            />
            <nav
              aria-label="视图"
              className="flex items-center rounded-[var(--radius-control)] bg-[rgba(224,223,228,0.6)] p-0.5"
            >
              {(["lanes", "leaderboard"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-current={tab === value ? "page" : undefined}
                  onClick={() => setTab(value)}
                  className={`min-h-10 rounded-[6px] px-3 text-[13px] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-accent)] ${
                    tab === value
                      ? "bg-[var(--color-surface)] font-medium shadow-[0_1px_1px_rgba(0,0,0,0.06)]"
                      : "text-[var(--color-ink-2)]"
                  }`}
                >
                  {value === "lanes" ? "实时赛道" : "正式榜单"}
                </button>
              ))}
            </nav>
          </div>
        </div>

        {snapshot && (
          <div className="border-t border-[var(--color-hairline)] bg-[rgba(255,255,255,0.9)]">
            <div className="mx-auto flex max-w-[1280px] flex-wrap items-center justify-between gap-x-6 gap-y-2 px-6 py-2">
              <div className="flex flex-wrap items-center gap-x-6 gap-y-1">
                <Meta label="当前 Run" value={snapshot.run_id} />
                <Meta label="用例总数" value={snapshot.execution.total.toLocaleString("en-US")} />
                <Meta label="Manifest" value={`${snapshot.manifest_hash.slice(0, 16)}…`} />
                <Meta label="内部状态" value={snapshot.internal_status} />
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <span className="tnum text-[11px] font-medium text-[var(--color-ink-3)]">
                  {snapshot.projection_reason}
                </span>
                <ProjectionChip snapshot={snapshot} />
              </div>
            </div>
          </div>
        )}
      </header>

      <main className="mx-auto max-w-[1280px] px-6 py-6">
        {runs.loading && <Panel title="正在加载 run 列表">读取 run 索引…</Panel>}

        {!runs.loading && runs.error != null && (
          <Panel title="无法连接 Arena 服务">
            请在本机启动服务后刷新：<code className="tnum">127.0.0.1:8765</code>
          </Panel>
        )}

        {!runs.loading && runs.error == null && (runs.data?.length ?? 0) === 0 && (
          <Panel title="暂无 run">该 Arena 目录下还没有 run，用 runner 执行一次后会在此出现。</Panel>
        )}

        {runId && loading && !snapshot && <Panel title="正在加载 run">获取快照…</Panel>}

        {runId && !loading && !snapshot && error != null && (
          <Panel title="快照不可用">
            服务端没有返回 <code className="tnum">{runId}</code> 的投影。
          </Panel>
        )}

        {snapshot && connection === "offline" && (
          <Banner tone="fail">
            已重连 5 次仍失败，实时更新已停止。下方为服务端最后一次发送的快照，刷新页面可重试。
          </Banner>
        )}

        {snapshot && connection === "stale" && (
          <Banner tone="warn">连接正常，但较长时间未收到持久事件，当前视图可能落后于 runner。</Banner>
        )}

        {snapshot && snapshot.status === "failed" && (
          <Banner tone="fail">
            执行失败。已记录的部分结果按原样展示，未运行的用例格不做任何推断。
          </Banner>
        )}

        {snapshot && snapshot.status === "incomplete" && (
          <Banner tone="warn">
            Run 未完成（{snapshot.projection_reason}）。评分器发布投影前，指标保持空白。
          </Banner>
        )}

        {snapshot && tab === "lanes" && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {lanes.map((variant) => (
              <LaneCard
                key={variant.variant_id}
                variant={variant}
                status={snapshot.status}
                selected={openVariant === variant.variant_id}
                onOpen={() => setOpenVariant(variant.variant_id)}
              />
            ))}
          </div>
        )}

        {snapshot && tab === "leaderboard" && (
          <Leaderboard snapshot={snapshot} onOpen={(v) => setOpenVariant(v.variant_id)} />
        )}
      </main>

      {selected && snapshot && (
        <VariantSheet
          key={selected.variant_id}
          variant={selected}
          snapshot={snapshot}
          cells={cellsByVariant.get(selected.variant_id) ?? []}
          onClose={closeSheet}
        />
      )}
    </div>
  );
}
