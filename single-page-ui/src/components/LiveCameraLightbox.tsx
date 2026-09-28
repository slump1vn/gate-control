'use client';

import { useEffect } from 'react';
import type { GateCamera } from '@/lib/gate-api';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import CameraStatusBadge from './CameraStatusBadge';
import LiveCameraView from './LiveCameraView';
import TriggerReadout from './TriggerReadout';
import { useI18n } from './I18nContext';
import { Badge } from './ui';

interface LiveCameraLightboxProps {
  camera: GateCamera;
  gateName: string;
  apiBase: string;
  intervalMs: number;
  showRoi: boolean;
  onClose: () => void;
  /** Fixed first frame, for Storybook. */
  initialSrc?: string;
}

/**
 * One lane's live view as large as the screen allows, for a closer look at a
 * vehicle or a plate. Closes on Escape, the close button or a click outside
 * the picture.
 */
export default function LiveCameraLightbox({
  camera, gateName, apiBase, intervalMs, showRoi, onClose, initialSrc,
}: LiveCameraLightboxProps) {
  const { t } = useI18n();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = overflow;
    };
  }, [onClose]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${gateName} · ${camera.name}`}
      className="fixed inset-0 z-[100] bg-black/90 flex items-center justify-center p-2 sm:p-4 cursor-zoom-out"
      onClick={onClose}
    >
      <div
        className="flex flex-col gap-2 cursor-default"
        // As wide as the screen, or as the height allows at 16:9 (header and readout take ~8rem)
        style={{ width: 'min(100%, calc((100vh - 8rem) * 16 / 9))' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex flex-wrap items-center gap-2 text-white">
          <Badge color={camera.direction === 'in' ? 'blue' : 'purple'}>
            {t(`gate.${camera.direction === 'in' ? 'entry' : 'exit'}` as keyof Dictionary)}
          </Badge>
          <span className="text-sm font-medium truncate">{gateName} · {camera.name}</span>
          <CameraStatusBadge status={camera.agent_status} />
          <button
            type="button"
            onClick={onClose}
            className="ml-auto text-white/70 hover:text-white transition-colors"
            aria-label={t('common.close')}
            autoFocus
          >
            <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <LiveCameraView
          cameraId={camera.id}
          apiBase={apiBase}
          intervalMs={intervalMs}
          streamUrl={camera.live_stream_url}
          roi={camera.roi}
          showRoi={showRoi}
          initialSrc={initialSrc}
        />
        <div className="rounded-lg bg-white/95 dark:bg-[#1e1e1e] px-3 py-2">
          <TriggerReadout readout={camera.agent_trigger} />
        </div>
      </div>
    </div>
  );
}
