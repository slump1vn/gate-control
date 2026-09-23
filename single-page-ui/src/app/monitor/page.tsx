'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { getApiBase } from '@/lib/api';
import { DIRECTION_LABELS, getAccessEvents, getGateStatus } from '@/lib/gate-api';
import type { AccessEvent, GateStatusResponse } from '@/lib/gate-api';
import { usePolling } from '@/hooks/usePolling';
import RequireRole from '@/components/RequireRole';
import LiveCameraView from '@/components/LiveCameraView';
import TriggerReadout from '@/components/TriggerReadout';
import BarrierArm from '@/components/BarrierArm';
import CameraStatusBadge from '@/components/CameraStatusBadge';
import { ConfidenceText, DecisionBadge, reasonLabel } from '@/components/EventBadges';
import Spinner from '@/components/Spinner';
import { Alert, Badge, PageHeader, cardClass, formatDateTime, inputClass } from '@/components/ui';

const STATUS_POLL_MS = 2000;
const EVENT_POLL_MS = 3000;

// The camera also serves the gate agent. Asking it for snapshots faster than
// once a second makes some models answer HTTP 500, so that is the ceiling here;
// the service caches frames and will not hit the camera more often anyway.
const INTERVALS = [
  { label: '1 frame/s', value: 1000 },
  { label: '1 frame / 2s', value: 2000 },
  { label: '1 frame / 5s', value: 5000 },
  { label: 'Paused', value: 0 },
];

function MonitorContent() {
  const [apiBase, setApiBase] = useState('');
  const [status, setStatus] = useState<GateStatusResponse | null>(null);
  const [events, setEvents] = useState<AccessEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [intervalMs, setIntervalMs] = useState(1000);
  const [showRoi, setShowRoi] = useState(true);

  useEffect(() => { getApiBase().then(setApiBase); }, []);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await getGateStatus());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load gate status');
    }
  }, []);

  const loadEvents = useCallback(async () => {
    try {
      setEvents((await getAccessEvents({ is_test: 'false', page_size: 8 })).results);
    } catch { /* the status panel already reports connection problems */ }
  }, []);

  usePolling(loadStatus, STATUS_POLL_MS);
  usePolling(loadEvents, EVENT_POLL_MS);

  const gates = status?.gates ?? [];

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title="Live monitor">
        <label className="text-sm text-gray-500 dark:text-gray-400" htmlFor="monitor-interval">Refresh</label>
        <select id="monitor-interval" className={`${inputClass} w-auto`} value={intervalMs}
          onChange={(e) => setIntervalMs(Number(e.target.value))}>
          {INTERVALS.map((i) => <option key={i.value} value={i.value}>{i.label}</option>)}
        </select>
        <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input type="checkbox" checked={showRoi} onChange={(e) => setShowRoi(e.target.checked)} className="w-4 h-4 accent-purple-600" />
          Read zone
        </label>
        <Link href="/gate" className="text-sm text-purple-600 dark:text-purple-400 hover:underline">Controls →</Link>
      </PageHeader>

      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      {!status ? (
        error ? null : <Spinner />
      ) : gates.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">No gates configured.</div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-8">
          {gates.map((gate) => (
            <section key={gate.id} className={`${cardClass} p-4`} aria-label={`Live view of ${gate.name}`}>
              <header className="flex flex-wrap items-center justify-between gap-2 mb-3">
                <div>
                  <h2 className="font-semibold">{gate.name}</h2>
                  <p className="text-xs text-gray-500 dark:text-gray-400">{gate.location}</p>
                </div>
                <Badge color={gate.online ? 'green' : 'red'}>{gate.online ? 'Controller online' : 'Controller offline'}</Badge>
              </header>

              {gate.cameras.length > 0 ? (
                <div className={`grid gap-3 ${gate.cameras.length > 1 ? 'sm:grid-cols-2' : ''}`}>
                  {gate.cameras.map((cam) => (
                    <div key={cam.id}>
                      <div className="flex flex-wrap items-center gap-2 mb-1">
                        <Badge color={cam.direction === 'in' ? 'blue' : 'purple'}>{DIRECTION_LABELS[cam.direction]}</Badge>
                        <span className="text-sm truncate">{cam.name}</span>
                        <span className="ml-auto flex gap-1">
                          {!cam.is_enabled && <Badge color="gray">Disabled</Badge>}
                          <CameraStatusBadge status={cam.agent_status} />
                        </span>
                      </div>
                      <LiveCameraView
                        cameraId={cam.id}
                        apiBase={apiBase}
                        intervalMs={intervalMs}
                        roi={cam.roi}
                        showRoi={showRoi}
                      />
                      <div className="mt-1"><TriggerReadout readout={cam.agent_trigger} /></div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="aspect-video rounded-lg bg-gray-100 dark:bg-[#2d2d2d] flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">
                  Assign cameras to this gate to see the lane
                </div>
              )}
              {gate.camera_warning && (
                <p className="mt-2 text-xs text-yellow-700 dark:text-yellow-400">{gate.camera_warning}</p>
              )}

              <div className="flex items-center gap-4 mt-3">
                <BarrierArm state={gate.simulator?.arm_state ?? gate.arm_state} position={gate.simulator?.position} size={110} />
                <div className="text-sm min-w-0">
                  <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Last decision</p>
                  {gate.last_event ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <DecisionBadge decision={gate.last_event.decision} />
                      {gate.last_event.decision !== 'manual' && (
                        <>
                          <span className="font-mono font-semibold">{gate.last_event.plate_normalized || '—'}</span>
                          <ConfidenceText value={gate.last_event.confidence} />
                        </>
                      )}
                      <span className="text-gray-600 dark:text-gray-300 truncate">{reasonLabel(gate.last_event)}</span>
                    </div>
                  ) : <p className="text-gray-400">No decisions yet</p>}
                </div>
              </div>
            </section>
          ))}
        </div>
      )}

      <section className={`${cardClass} p-4`}>
        <h2 className="font-semibold mb-3">Recent events</h2>
        {events.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">Nothing yet.</p>
        ) : (
          <ul className="divide-y divide-gray-200 dark:divide-gray-700 text-sm">
            {events.map((e) => (
              <li key={e.id} className="py-2 flex flex-wrap items-center gap-2">
                <span className="text-gray-500 dark:text-gray-400 w-40">{formatDateTime(e.timestamp)}</span>
                <DecisionBadge decision={e.decision} />
                <span className="font-mono font-semibold">{e.plate_normalized || '—'}</span>
                <span className="text-gray-600 dark:text-gray-300">{reasonLabel(e)}</span>
                {e.vehicle && <span className="text-gray-500 dark:text-gray-400">· {e.vehicle.owner_name}</span>}
                <span className="text-gray-400 ml-auto">{e.gate?.name}</span>
              </li>
            ))}
          </ul>
        )}
        <Link href="/events" className="inline-block mt-3 text-sm text-purple-600 dark:text-purple-400 hover:underline">
          All events →
        </Link>
      </section>
    </div>
  );
}

export default function MonitorPage() {
  return (
    <RequireRole role="gate_operator">
      <MonitorContent />
    </RequireRole>
  );
}
