import { describe, expect, it } from "vitest";
import {
  NOT_AVAILABLE,
  hasProjection,
  partitionForLeaderboard,
  percent,
  progressPercent,
  sumCounters,
} from "./format";
import {
  MAX_BACKOFF_MS,
  backoffMs,
  createRunStream,
  nextCursor,
  type StreamSource,
} from "./stream";
import type { RunSnapshot, RunStatus, Variant } from "./types";

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

function snapshot(overrides: Partial<RunSnapshot> = {}): RunSnapshot {
  return {
    schema_version: "qveris-run-snapshot/v1",
    run_id: "score-run",
    manifest_hash: "a".repeat(64),
    status: "running" as RunStatus,
    snapshot_sequence: 3,
    event_cursor: 3,
    updated_at: 1_788_436_191,
    connection_basis: "durable_event_journal",
    projection_status: "UNSCORED",
    projection_reason: "scorer_projection_unavailable",
    internal_status: "running",
    variants: [],
    cells: [],
    execution: { total: 6, completed: 0, success: 0, failed: 0, incomplete: 0, blocked: 0 },
    scoring: {
      semantic_accuracy: "UNSCORED",
      data_accuracy: "UNSCORED",
      end_to_end_latency: "UNSCORED",
      token_usage: "UNSCORED",
      coverage: null,
      rank: null,
      eligibility: null,
    },
    ...overrides,
  };
}

/** Records every URL opened and lets a test push frames at the client. */
function harness(snapshots: RunSnapshot[]) {
  const urls: string[] = [];
  const sources: {
    url: string;
    listeners: Map<string, (event: MessageEvent) => void>;
    closed: boolean;
  }[] = [];
  const received: RunSnapshot[] = [];
  const states: string[] = [];
  let fetches = 0;

  const deps = {
    open(url: string): StreamSource {
      urls.push(url);
      const entry = { url, listeners: new Map<string, (event: MessageEvent) => void>(), closed: false };
      sources.push(entry);
      return {
        addEventListener: (type: string, listener: (event: MessageEvent) => void) =>
          void entry.listeners.set(type, listener),
        close: () => void (entry.closed = true),
      };
    },
    fetchSnapshot: async () => snapshots[Math.min(fetches++, snapshots.length - 1)],
    schedule: (fn: () => void, ms: number) => setTimeout(fn, ms),
    cancel: (token: unknown) => clearTimeout(token as ReturnType<typeof setTimeout>),
  };

  const handle = createRunStream("score-run", deps, {
    onSnapshot: (value) => received.push(value),
    onConnection: (state) => states.push(state),
    onError: () => states.push("error"),
  });

  const emit = (name: string, data: unknown, lastEventId?: string) =>
    sources.at(-1)?.listeners.get(name)?.({ data: JSON.stringify(data), lastEventId } as MessageEvent);

  return { urls, sources, received, states, handle, emit, fetchCount: () => fetches };
}

describe("backoff", () => {
  it("grows geometrically from 250ms and is capped", () => {
    expect([1, 2, 3, 4, 5].map(backoffMs)).toEqual([250, 500, 1000, 2000, 4000]);
    expect(backoffMs(50)).toBe(MAX_BACKOFF_MS);
  });
});

describe("nextCursor", () => {
  it("advances only on a well-formed, forward sequence id", () => {
    expect(nextCursor(3, "7")).toBe(7);
    expect(nextCursor(7, "3")).toBe(7); // never rewinds
    expect(nextCursor(7, "0x9")).toBe(7); // server ids are ASCII decimal
    expect(nextCursor(7, null)).toBe(7);
    expect(nextCursor(7, "")).toBe(7);
  });
});

describe("createRunStream", () => {
  it("baselines from the snapshot before opening the stream", async () => {
    const h = harness([snapshot()]);
    await flush();
    expect(h.received).toHaveLength(1);
    // Cursor came from the snapshot, so the stream resumes rather than replaying.
    expect(h.urls).toEqual(["/v1/arena/runs/score-run/events?after=3"]);
    h.handle.close();
  });

  it("treats a durable event as a refetch signal, never as a delta", async () => {
    const h = harness([snapshot(), snapshot({ snapshot_sequence: 4, event_cursor: 4 })]);
    await flush();
    const before = h.fetchCount();
    // `terminal` carries no variant_id/case_id, so it cannot be applied locally.
    h.emit("terminal", { elapsed_ms: 1, transport_status: "completed" }, "4");
    await flush();
    expect(h.fetchCount()).toBe(before + 1);
    expect(h.received.at(-1)?.snapshot_sequence).toBe(4);
    h.handle.close();
  });

  it("re-baselines from sequence 0 on resync_required", async () => {
    const h = harness([snapshot(), snapshot({ snapshot_sequence: 9, event_cursor: 9 })]);
    await flush();
    h.emit("resync_required", { snapshot_url: "/v1/arena/runs/score-run/snapshot" });
    await flush();
    await flush();
    expect(h.urls.at(-1)).toBe("/v1/arena/runs/score-run/events?after=9");
    h.handle.close();
  });

  it("closes the stream itself on `completed`, which the server keeps open", async () => {
    const h = harness([snapshot({ status: "completed", projection_status: "SCORED" })]);
    await flush();
    expect(h.sources.at(-1)?.closed ?? true).toBe(true);
    expect(h.states).toContain("closed");
  });

  it("gives up after 5 reconnects and reports offline", async () => {
    const h = harness([snapshot()]);
    await flush();
    for (let i = 0; i < 6; i += 1) {
      h.sources.at(-1)?.listeners.get("error")?.({} as MessageEvent);
      await new Promise((resolve) => setTimeout(resolve, 0));
    }
    expect(h.states).toContain("offline");
    h.handle.close();
  });
});

describe("never fabricating a metric", () => {
  it("renders an absent value as text, not as zero", () => {
    expect(percent(undefined)).toBe(NOT_AVAILABLE);
    expect(percent(null)).toBe(NOT_AVAILABLE);
    expect(percent(0)).toBe("0.0%"); // a real zero is still a real result
    expect(percent(1)).toBe("100.0%");
  });

  it("only ranks variants the scorer actually ranked", () => {
    const variants = [
      { variant_id: "b", stable_display_order: 2, suites: {}, rank: 2, eligibility: "eligible" },
      { variant_id: "a", stable_display_order: 1, suites: {}, rank: 1, eligibility: "eligible" },
      { variant_id: "c", stable_display_order: 3, suites: {}, eligibility: "not_ranked" },
      { variant_id: "d", stable_display_order: 4, suites: {}, eligibility: "ineligible" },
    ] as unknown as Variant[];

    const { ranked, unranked, ineligible } = partitionForLeaderboard(variants);
    expect(ranked.map((v) => v.variant_id)).toEqual(["a", "b"]);
    expect(unranked.map((v) => v.variant_id)).toEqual(["c"]);
    expect(ineligible.map((v) => v.variant_id)).toEqual(["d"]);
    // The unranked variant is not appended to the bottom of the ranking.
    expect(ranked).toHaveLength(2);
  });

  it("sums suite counters as progress without inventing a suite", () => {
    const summed = sumCounters({
      realtime_quote: { total: 3, completed: 2, success: 1, failed: 1, incomplete: 0, blocked: 0 },
      financial_statements: { total: 1, completed: 1, success: 0, failed: 0, incomplete: 0, blocked: 1 },
    });
    expect(summed).toEqual({ total: 4, completed: 3, success: 1, failed: 1, incomplete: 0, blocked: 1 });
    expect(progressPercent(3, 4)).toBe("75.0%");
    expect(progressPercent(0, 0)).toBe("0.0%"); // no division-by-zero NaN in the UI
  });

  it("shows the leaderboard only when a projection exists", () => {
    expect(hasProjection({ projection_status: "UNSCORED" })).toBe(false);
    expect(hasProjection({ projection_status: "SCORED" })).toBe(true);
    expect(hasProjection({ projection_status: "SCORED_NOT_RANKED" })).toBe(true);
  });
});
