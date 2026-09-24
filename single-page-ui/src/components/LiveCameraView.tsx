'use client';

import { useEffect, useRef, useState } from 'react';
import type { Roi } from '@/lib/gate-api';
import { cameraSnapshotError, cameraSnapshotPath } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import { Badge } from './ui';

interface LiveCameraViewProps {
  cameraId: number;
  /** API base URL ('' = same origin). */
  apiBase: string;
  /** Milliseconds between frames; 0 pauses the view. */
  intervalMs: number;
  roi?: Roi | null;
  showRoi?: boolean;
  /** Fixed first frame, for Storybook. */
  initialSrc?: string;
  /** Looks up why a frame failed; defaults to the API. */
  errorLookup?: (cameraId: number) => Promise<string>;
}

const ERROR_BACKOFF_MS = 3000;

/**
 * Live view of a gate lane. Browsers cannot play RTSP, so the LPR service
 * fetches snapshots from the camera and this polls that endpoint, waiting for
 * each frame to load before asking for the next one.
 */
export default function LiveCameraView({
  cameraId, apiBase, intervalMs, roi, showRoi = true, initialSrc, errorLookup = cameraSnapshotError,
}: LiveCameraViewProps) {
  const { t } = useI18n();
  const [src, setSrc] = useState<string | null>(initialSrc ?? null);
  const [error, setError] = useState<string | null>(null);
  // True when no frame has arrived for a while: the camera or the agent is stuck.
  const [stale, setStale] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stopped = useRef(false);

  const next = (delay: number) => {
    if (stopped.current || intervalMs <= 0) return;
    timer.current = setTimeout(() => {
      if (typeof document !== 'undefined' && document.visibilityState !== 'visible') {
        next(delay);
        return;
      }
      setSrc(`${apiBase}${cameraSnapshotPath(cameraId)}?t=${Date.now()}`);
    }, delay);
  };

  useEffect(() => {
    stopped.current = false;
    // Scheduled rather than set directly, so the first frame is requested
    // after the effect commits.
    if (intervalMs > 0) {
      timer.current = setTimeout(() => setSrc(`${apiBase}${cameraSnapshotPath(cameraId)}?t=${Date.now()}`), 0);
    }
    return () => {
      stopped.current = true;
      if (timer.current) clearTimeout(timer.current);
      if (staleTimer.current) clearTimeout(staleTimer.current);
    };
  }, [cameraId, apiBase, intervalMs]);

  const onLoad = () => {
    setError(null);
    setStale(false);
    if (staleTimer.current) clearTimeout(staleTimer.current);
    if (intervalMs > 0) {
      staleTimer.current = setTimeout(() => setStale(true), Math.max(intervalMs * 3, 5000));
    }
    next(intervalMs);
  };

  const onError = () => {
    // The <img> cannot read the body, so ask the endpoint why.
    errorLookup(cameraId).then((message) => setError(message || 'No frame from the camera.')).catch(() => {});
    next(ERROR_BACKOFF_MS);
  };

  return (
    <div className="relative bg-gray-900 rounded-lg overflow-hidden aspect-video flex items-center justify-center">
      {src && (
        // eslint-disable-next-line @next/next/no-img-element -- session-authenticated API image, refreshed by src
        <img src={src} alt="Live camera view" onLoad={onLoad} onError={onError}
          className={`w-full h-full object-contain ${error ? 'opacity-30' : ''}`} />
      )}

      {showRoi && roi && !error && (
        <div
          className="absolute border-2 border-yellow-400 pointer-events-none"
          style={{ left: `${roi.x * 100}%`, top: `${roi.y * 100}%`, width: `${roi.w * 100}%`, height: `${roi.h * 100}%` }}
        >
          <span className="absolute -top-5 left-0 text-[10px] bg-yellow-400 text-black px-1 rounded">{t('roi.readZone')}</span>
        </div>
      )}

      {intervalMs <= 0 && !src && (
        <p className="text-sm text-gray-400">{t('live.paused')}</p>
      )}

      {error && (
        <div className="absolute inset-x-0 bottom-0 p-2 bg-red-900/80 text-red-100 text-xs">
          {error}
          {/HTTP 5\d\d|timed out/i.test(error) && (
            <span className="block text-red-200/80">{t('live.cameraRefusing')}</span>
          )}
        </div>
      )}

      {!error && src && (
        <div className="absolute top-2 right-2">
          <Badge color={stale ? 'yellow' : 'green'}>{t(stale ? 'live.stale' : 'live.live')}</Badge>
        </div>
      )}
    </div>
  );
}
