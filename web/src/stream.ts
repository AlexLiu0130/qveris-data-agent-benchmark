import { eventsUrl } from "./api";
import { isTerminal, type RunSnapshot } from "./types";

/**
 * SSE consumption for a single run.
 *
 * Two facts about the server shape this file:
 *
 * 1. Durable `terminal` events carry no variant_id/case_id, so an event can
 *    never be applied as a delta. The snapshot is the only source of truth;
 *    an event is nothing but a signal to re-fetch it.
 * 2. The server closes the stream for `failed`/`incomplete` but NOT for
 *    `completed` — it heartbeats forever. So the client decides when a run is
 *    over and closes the connection itself.
 *
 * EventSource's own reconnect is unbounded and replays from `Last-Event-ID`,
 * which we do not want; every path here calls close() and reopens with an
 * explicit `?after=` cursor so reconnects stay counted and bounded.
 */

export const MAX_RECONNECTS = 5;
export const BASE_BACKOFF_MS = 250;
export const MAX_BACKOFF_MS = 4_000;
/** No durable event for this long on a live run means the view may be behind. */
export const STALE_AFTER_MS = 30_000;

export type ConnectionState =
  | "connecting"
  | "live"
  | "reconnecting"
  | "stale"
  | "offline"
  /** Run reached a terminal status; the stream was closed deliberately. */
  | "closed";

export function backoffMs(attempt: number): number {
  return Math.min(BASE_BACKOFF_MS * 2 ** Math.max(0, attempt - 1), MAX_BACKOFF_MS);
}

/**
 * `lastEventId` is the SSE `id:` line, which the server sets to the durable
 * sequence. Anything unparseable leaves the cursor where it was so a reconnect
 * re-reads rather than skips.
 */
export function nextCursor(current: number, lastEventId: string | null | undefined): number {
  if (!lastEventId || !/^\d+$/.test(lastEventId)) return current;
  const parsed = Number(lastEventId);
  return Number.isSafeInteger(parsed) && parsed > current ? parsed : current;
}

/** Minimal surface of EventSource, so tests can drive this without a browser. */
export interface StreamSource {
  addEventListener(type: string, listener: (event: MessageEvent) => void): void;
  close(): void;
}

export interface StreamHandle {
  close(): void;
}

export interface StreamDeps {
  open(url: string): StreamSource;
  fetchSnapshot(runId: string): Promise<RunSnapshot>;
  schedule(fn: () => void, ms: number): unknown;
  cancel(token: unknown): void;
}

export interface StreamCallbacks {
  onSnapshot(snapshot: RunSnapshot): void;
  onConnection(state: ConnectionState, detail?: { attempt: number }): void;
  onError(error: unknown): void;
}

const DURABLE_EVENTS = ["run_started", "dispatch_intent", "terminal", "run_finished", "scorer_projection"];

export function createRunStream(
  runId: string,
  deps: StreamDeps,
  callbacks: StreamCallbacks,
): StreamHandle {
  let cursor = 0;
  let attempt = 0;
  let source: StreamSource | null = null;
  let retryToken: unknown = null;
  let disposed = false;
  /** Serialises snapshot re-fetches so a burst of events cannot interleave. */
  let refreshing: Promise<void> = Promise.resolve();

  const drop = () => {
    source?.close();
    source = null;
    if (retryToken !== null) {
      deps.cancel(retryToken);
      retryToken = null;
    }
  };

  const stop = () => {
    disposed = true;
    drop();
  };

  const refresh = () => {
    refreshing = refreshing.then(async () => {
      if (disposed) return;
      try {
        const snapshot = await deps.fetchSnapshot(runId);
        if (disposed) return;
        apply(snapshot);
      } catch (error) {
        if (!disposed) callbacks.onError(error);
      }
    });
  };

  const apply = (snapshot: RunSnapshot) => {
    cursor = Math.max(cursor, snapshot.snapshot_sequence);
    callbacks.onSnapshot(snapshot);
    if (isTerminal(snapshot.status)) {
      // The server keeps a `completed` stream open forever. We do not.
      stop();
      callbacks.onConnection("closed");
    }
  };

  const retry = () => {
    drop();
    if (disposed) return;
    attempt += 1;
    if (attempt > MAX_RECONNECTS) {
      callbacks.onConnection("offline", { attempt });
      return;
    }
    callbacks.onConnection("reconnecting", { attempt });
    retryToken = deps.schedule(() => {
      retryToken = null;
      connect();
    }, backoffMs(attempt));
  };

  function connect() {
    if (disposed) return;
    callbacks.onConnection(cursor === 0 ? "connecting" : "reconnecting", { attempt });
    const es = deps.open(eventsUrl(runId, cursor));
    source = es;

    es.addEventListener("open", () => {
      if (disposed) return;
      attempt = 0;
      callbacks.onConnection("live", { attempt });
    });

    es.addEventListener("snapshot", (event) => {
      if (disposed) return;
      attempt = 0;
      try {
        apply(JSON.parse(event.data) as RunSnapshot);
      } catch (error) {
        callbacks.onError(error);
        return;
      }
      if (!disposed) callbacks.onConnection("live", { attempt });
    });

    es.addEventListener("resync_required", () => {
      if (disposed) return;
      // Our cursor is ahead of, or discontinuous with, the durable journal.
      // Re-baseline from the snapshot and reopen from scratch.
      drop();
      cursor = 0;
      refresh();
      refreshing.then(() => {
        if (!disposed) connect();
      });
    });

    for (const name of DURABLE_EVENTS) {
      es.addEventListener(name, (event) => {
        if (disposed) return;
        attempt = 0;
        cursor = nextCursor(cursor, event.lastEventId);
        // Events are signals, never deltas — see the note at the top.
        refresh();
      });
    }

    es.addEventListener("error", () => {
      if (disposed) return;
      retry();
    });
  }

  // Baseline before the stream so the UI has data even if SSE never opens.
  refresh();
  refreshing.then(() => {
    if (!disposed) connect();
  });

  return { close: stop };
}
