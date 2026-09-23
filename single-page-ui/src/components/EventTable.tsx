'use client';

import { useState } from 'react';
import type { AccessEvent } from '@/lib/gate-api';
import { DIRECTION_LABELS, eventImagePath } from '@/lib/gate-api';
import { CommandBadge, ConfidenceText, DecisionBadge, reasonLabel } from './EventBadges';
import { Badge, formatDateTime } from './ui';

interface EventTableProps {
  events: AccessEvent[];
  /** API base URL for frame thumbnails ('' = same origin). */
  apiBase: string;
  onOpenAnyway?: (event: AccessEvent) => void;
  onPreview?: (src: string, alt: string) => void;
}

/** Denied reads worth a one-click manual open: near misses and plain denials at a known gate. */
export function canOpenAnyway(e: AccessEvent): boolean {
  return !!e.gate && e.decision === 'denied' && !e.is_test;
}

function Thumbnail({ event, apiBase, onPreview }: { event: AccessEvent; apiBase: string; onPreview?: EventTableProps['onPreview'] }) {
  const [failed, setFailed] = useState(false);
  if (!event.has_image || failed) {
    return <div className="w-24 h-16 rounded bg-gray-100 dark:bg-[#2d2d2d] flex items-center justify-center text-xs text-gray-400">no image</div>;
  }
  const type = event.has_processed_image ? 'processed' : 'original';
  const src = `${apiBase}${eventImagePath(event.id, type)}`;
  const full = `${apiBase}${eventImagePath(event.id, 'original')}`;
  const alt = `Frame for event ${event.id}`;
  return (
    <button type="button" onClick={() => onPreview?.(event.has_processed_image ? src : full, alt)} className="block cursor-zoom-in">
      {/* eslint-disable-next-line @next/next/no-img-element -- session-authenticated API image */}
      <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} className="w-24 h-16 object-cover rounded" />
    </button>
  );
}

export default function EventTable({ events, apiBase, onOpenAnyway, onPreview }: EventTableProps) {
  return (
    <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-xl">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50 dark:bg-[#1a1a1a] text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
          <tr>
            <th className="px-3 py-3">Frame</th>
            <th className="px-3 py-3">Time / gate</th>
            <th className="px-3 py-3">Plate</th>
            <th className="px-3 py-3">Decision</th>
            <th className="px-3 py-3 hidden lg:table-cell">Barrier</th>
            <th className="px-3 py-3 text-right"><span className="sr-only">Actions</span></th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
          {events.map((e) => (
            <tr key={e.id} className="align-top">
              <td className="px-3 py-2"><Thumbnail event={e} apiBase={apiBase} onPreview={onPreview} /></td>
              <td className="px-3 py-2">
                <div className="whitespace-nowrap">{formatDateTime(e.timestamp)}</div>
                <div className="text-xs text-gray-500 dark:text-gray-400 flex flex-wrap items-center gap-1">
                  <span>{e.gate?.name ?? '—'}</span>
                  {e.direction && <Badge color={e.direction === 'in' ? 'blue' : 'purple'}>{DIRECTION_LABELS[e.direction]}</Badge>}
                  {e.camera && <span className="truncate max-w-[8rem]">{e.camera.name}</span>}
                  {e.is_test && <Badge color="purple">test</Badge>}
                </div>
              </td>
              <td className="px-3 py-2">
                {e.decision === 'manual' ? <span className="text-gray-400">—</span> : (
                  <>
                    <div className="font-mono font-semibold">{e.plate_normalized || e.plate_raw || '—'}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400">
                      <ConfidenceText value={e.confidence} />
                      {e.frames_read > 0 && <> · {e.frames_agreed}/{e.frames_read} frames</>}
                    </div>
                  </>
                )}
              </td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap items-center gap-1">
                  <DecisionBadge decision={e.decision} />
                  <span>{reasonLabel(e)}</span>
                </div>
                <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                  {e.vehicle && <>{e.vehicle.owner_name} ({e.vehicle.plate_display})</>}
                  {e.near_miss_vehicle && <span className="text-yellow-700 dark:text-yellow-400">Close to {e.near_miss_vehicle.plate_display} ({e.near_miss_vehicle.owner_name})</span>}
                  {e.operator && <>by {e.operator}</>}
                </div>
              </td>
              <td className="px-3 py-2 hidden lg:table-cell"><CommandBadge event={e} /></td>
              <td className="px-3 py-2 text-right">
                {onOpenAnyway && canOpenAnyway(e) && (
                  <button
                    onClick={() => onOpenAnyway(e)}
                    className={`whitespace-nowrap px-3 py-1.5 text-xs rounded-lg font-medium ${e.near_miss_vehicle
                      ? 'bg-yellow-500 text-white hover:bg-yellow-600'
                      : 'border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-[#2d2d2d]'}`}
                  >
                    Open anyway
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
