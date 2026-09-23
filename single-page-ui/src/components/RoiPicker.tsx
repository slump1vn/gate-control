'use client';

import { PointerEvent, useRef, useState } from 'react';
import type { Roi } from '@/lib/gate-api';

interface RoiPickerProps {
  /** Camera snapshot to draw on (data: URL from the connection test). */
  image: string;
  roi: Roi | null;
  onChange: (roi: Roi | null) => void;
  /** When false the zone is shown but cannot be redrawn. */
  editing?: boolean;
  /** Told while a rectangle is being dragged, so a live image can hold still. */
  onDraggingChange?: (dragging: boolean) => void;
}

const MIN_SIZE = 0.03;

const clamp = (v: number) => Math.min(1, Math.max(0, v));
const round = (v: number) => Math.round(v * 1000) / 1000;

/**
 * Drag a rectangle on the camera image to set the read zone. Coordinates are
 * fractions of the frame (0–1), independent of the stream resolution.
 */
export default function RoiPicker({ image, roi, onChange, editing = true, onDraggingChange }: RoiPickerProps) {
  const box = useRef<HTMLDivElement>(null);
  const [start, setStart] = useState<{ x: number; y: number } | null>(null);
  const [draft, setDraft] = useState<Roi | null>(null);

  const point = (e: PointerEvent) => {
    const r = box.current!.getBoundingClientRect();
    return { x: clamp((e.clientX - r.left) / r.width), y: clamp((e.clientY - r.top) / r.height) };
  };

  const rect = (a: { x: number; y: number }, b: { x: number; y: number }): Roi => ({
    x: round(Math.min(a.x, b.x)),
    y: round(Math.min(a.y, b.y)),
    w: round(Math.abs(a.x - b.x)),
    h: round(Math.abs(a.y - b.y)),
  });

  const onDown = (e: PointerEvent) => {
    if (!editing) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    const p = point(e);
    setStart(p);
    setDraft({ x: p.x, y: p.y, w: 0, h: 0 });
    onDraggingChange?.(true);
  };

  const onMove = (e: PointerEvent) => {
    if (start) setDraft(rect(start, point(e)));
  };

  const onUp = (e: PointerEvent) => {
    if (!start) return;
    const next = rect(start, point(e));
    setStart(null);
    setDraft(null);
    onDraggingChange?.(false);
    // A click or a sliver is not a zone: keep the previous one.
    if (next.w >= MIN_SIZE && next.h >= MIN_SIZE) onChange(next);
  };

  const shown = draft ?? roi;

  return (
    <div>
      <div
        ref={box}
        className={`relative select-none touch-none overflow-hidden rounded-lg border border-gray-300 dark:border-gray-600 ${editing ? 'cursor-crosshair' : ''}`}
        onPointerDown={onDown}
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={() => { setStart(null); setDraft(null); onDraggingChange?.(false); }}
        role={editing ? 'application' : undefined}
        aria-label={editing ? 'Drag on the image to set the read zone' : 'Camera snapshot with read zone'}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- data: URL from the camera test */}
        <img src={image} alt="Camera snapshot" draggable={false} className="block w-full h-auto" />
        {shown && (
          <div
            className="absolute border-2 border-yellow-400 pointer-events-none"
            style={{
              left: `${shown.x * 100}%`, top: `${shown.y * 100}%`, width: `${shown.w * 100}%`, height: `${shown.h * 100}%`,
              // Dim everything outside the zone
              boxShadow: '0 0 0 9999px rgba(0, 0, 0, 0.45)',
            }}
          >
            <span className="absolute -top-6 left-0 text-xs bg-yellow-400 text-black px-1 rounded">Read zone</span>
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 mt-2 text-xs text-gray-500 dark:text-gray-400">
        <span>
          {roi
            ? `x ${roi.x} · y ${roi.y} · w ${roi.w} · h ${roi.h}`
            : 'No read zone: the whole frame is used.'}
        </span>
        {editing && roi && (
          <button type="button" onClick={() => onChange(null)} className="text-purple-600 dark:text-purple-400 hover:underline">
            Reset to whole frame
          </button>
        )}
      </div>
    </div>
  );
}
