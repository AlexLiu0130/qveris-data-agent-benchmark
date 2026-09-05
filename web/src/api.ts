import type { RunListResponse, RunSnapshot, VariantDetailResponse } from "./types";

/**
 * The four read-only routes. Nothing else is ever called.
 *
 * No custom request headers anywhere in this file: the arena server implements
 * no OPTIONS route, so any header that would trigger a CORS preflight turns a
 * working request into a failed one. Same reason `credentials` is left default.
 */

// ponytail: relative paths, proxied by Vite in dev. Same-origin in a build, so
// there is no base-URL config to get wrong and no CORS to negotiate.
const BASE = "/v1/arena";

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string) {
    super(`${status} ${code}`);
    this.name = "ApiError";
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal, cache: "no-store" });
  if (!res.ok) {
    // The server answers {"error": "..."} for every failure it authors.
    const code = await res
      .json()
      .then((body: unknown) =>
        body && typeof body === "object" && typeof (body as { error?: unknown }).error === "string"
          ? (body as { error: string }).error
          : "unknown_error",
      )
      .catch(() => "unknown_error");
    throw new ApiError(res.status, code);
  }
  return (await res.json()) as T;
}

export const listRuns = (signal?: AbortSignal) => getJson<RunListResponse>("/runs", signal);

export const getSnapshot = (runId: string, signal?: AbortSignal) =>
  getJson<RunSnapshot>(`/runs/${encodeURIComponent(runId)}/snapshot`, signal);

export const getVariant = (runId: string, variantId: string, signal?: AbortSignal) =>
  getJson<VariantDetailResponse>(
    `/runs/${encodeURIComponent(runId)}/variants/${encodeURIComponent(variantId)}`,
    signal,
  );

export const eventsUrl = (runId: string, after: number) =>
  `${BASE}/runs/${encodeURIComponent(runId)}/events?after=${after}`;
