import { useEffect, useRef } from 'react';

/**
 * Call `tick` now, again whenever `key` changes, and every `intervalMs` while
 * the tab is visible (pass null to load without repeating). A tick never
 * overlaps the previous one, so a slow backend is not piled up on.
 */
export function usePolling(tick: () => Promise<unknown>, intervalMs: number | null, key: unknown = null) {
  const tickRef = useRef(tick);
  useEffect(() => { tickRef.current = tick; }, [tick]);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;

    const run = async () => {
      if (stopped) return;
      try { await tickRef.current(); } catch { /* the tick reports its own errors */ }
      if (!stopped && intervalMs !== null) timer = setTimeout(schedule, intervalMs);
    };
    // Hidden tabs skip ticks but keep the schedule, and catch up when shown.
    const schedule = () => {
      if (document.visibilityState === 'visible') run();
      else if (!stopped && intervalMs !== null) timer = setTimeout(schedule, intervalMs);
    };
    const onVisible = () => {
      if (document.visibilityState === 'visible' && timer && intervalMs !== null) {
        clearTimeout(timer);
        timer = null;
        run();
      }
    };

    run();
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [intervalMs, key]);
}
