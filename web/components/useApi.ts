"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getJson } from "@/lib/api";
import { useLive } from "./Live";

export type ApiState<T> = {
  data: T | null;
  error: string | null;
  loadedAt: number;
  reload: () => void;
};

/**
 * Fetch `path`, then again whenever a live event arrives (debounced) and, if `pollMs` is set,
 * on that interval. Keeps the last good data on a failed refresh so the page doesn't blank out.
 */
export function useApi<T>(path: string | null, pollMs = 0, debounceMs = 500): ApiState<T> {
  const { tick } = useLive();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadedAt, setLoadedAt] = useState(0);
  const first = useRef(true);

  const load = useCallback(() => {
    if (!path) return;
    getJson<T>(path)
      .then((d) => {
        setData(d);
        setError(null);
        setLoadedAt(Date.now());
      })
      .catch((e: Error) => setError(e.message));
  }, [path]);

  useEffect(() => {
    first.current = true;
    load();
  }, [load]);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const t = setTimeout(load, debounceMs);
    return () => clearTimeout(t);
  }, [tick, load, debounceMs]);

  useEffect(() => {
    if (!pollMs) return;
    const id = setInterval(load, pollMs);
    return () => clearInterval(id);
  }, [load, pollMs]);

  return { data, error, loadedAt, reload: load };
}

/** A clock that re-renders the caller every `ms`, for "x seconds ago" labels. */
export function useNow(ms = 5000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}
