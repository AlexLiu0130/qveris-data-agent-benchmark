import { useCallback, useEffect, useRef, useState } from "react";
import { getSnapshot, listRuns } from "./api";
import { createRunStream, STALE_AFTER_MS, type ConnectionState } from "./stream";
import type { RunSnapshot, RunSummary } from "./types";

export interface Async<T> {
  data: T | null;
  error: unknown;
  loading: boolean;
}

export function useRuns(): Async<RunSummary[]> & { reload: () => void } {
  const [state, setState] = useState<Async<RunSummary[]>>({ data: null, error: null, loading: true });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState((prev) => ({ ...prev, loading: true }));
    listRuns(controller.signal)
      .then((body) => setState({ data: body.runs, error: null, loading: false }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ data: null, error, loading: false });
      });
    return () => controller.abort();
  }, [nonce]);

  return { ...state, reload: useCallback(() => setNonce((n) => n + 1), []) };
}

export interface RunStreamState {
  snapshot: RunSnapshot | null;
  connection: ConnectionState;
  attempt: number;
  error: unknown;
  loading: boolean;
  /** Wall-clock ms of the last snapshot we accepted, for the "updated Xs ago" line. */
  receivedAt: number | null;
}

/**
 * Subscribes to one run. Every durable event triggers a full snapshot re-fetch,
 * so what renders is always a projection the server actually authored.
 */
export function useRunStream(runId: string | null): RunStreamState {
  const [state, setState] = useState<RunStreamState>({
    snapshot: null,
    connection: "connecting",
    attempt: 0,
    error: null,
    loading: true,
    receivedAt: null,
  });
  const staleTimer = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (!runId) return;
    setState({
      snapshot: null,
      connection: "connecting",
      attempt: 0,
      error: null,
      loading: true,
      receivedAt: null,
    });

    const armStale = () => {
      window.clearTimeout(staleTimer.current);
      staleTimer.current = window.setTimeout(() => {
        setState((prev) => (prev.connection === "live" ? { ...prev, connection: "stale" } : prev));
      }, STALE_AFTER_MS);
    };

    const handle = createRunStream(
      runId,
      {
        open: (url) => new EventSource(url),
        fetchSnapshot: (id) => getSnapshot(id),
        schedule: (fn, ms) => window.setTimeout(fn, ms),
        cancel: (token) => window.clearTimeout(token as number),
      },
      {
        onSnapshot: (snapshot) => {
          armStale();
          setState((prev) => ({
            ...prev,
            snapshot,
            error: null,
            loading: false,
            receivedAt: Date.now(),
          }));
        },
        onConnection: (connection, detail) =>
          setState((prev) => {
            // A closed/offline stream must not be re-labelled "live" by a late frame.
            if (prev.connection === "closed" && connection !== "closed") return prev;
            return { ...prev, connection, attempt: detail?.attempt ?? prev.attempt };
          }),
        onError: (error) => setState((prev) => ({ ...prev, error, loading: false })),
      },
    );

    const offline = () => setState((prev) => ({ ...prev, connection: "offline" }));
    window.addEventListener("offline", offline);
    return () => {
      window.removeEventListener("offline", offline);
      window.clearTimeout(staleTimer.current);
      handle.close();
    };
  }, [runId]);

  return state;
}

/** Re-renders on an interval so relative timestamps do not freeze. */
export function useTicker(ms = 5_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}
