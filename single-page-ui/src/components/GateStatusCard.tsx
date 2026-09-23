'use client';

import { useState } from 'react';
import { DIRECTION_LABELS } from '@/lib/gate-api';
import type { Command, GateStatus } from '@/lib/gate-api';
import BarrierArm from './BarrierArm';
import CameraStatusBadge from './CameraStatusBadge';
import { CommandBadge, ConfidenceText, DecisionBadge, reasonLabel } from './EventBadges';
import { formatRelativeTime } from '@/lib/relative-time';
import { Badge, cardClass, dangerButton, secondaryButton } from './ui';

interface GateStatusCardProps {
  gate: GateStatus;
  mode: 'shadow' | 'live';
  onCommand: (gateId: number, command: Command) => Promise<void>;
}

export default function GateStatusCard({ gate, mode, onCommand }: GateStatusCardProps) {
  const [busy, setBusy] = useState<Command | null>(null);
  const simulated = gate.controller_type === 'simulator';
  // Shadow mode forbids physical actuation only: simulated gates still move.
  const commandsLogged = mode === 'shadow' && !simulated;
  const last = gate.last_event;
  const armState = gate.simulator?.arm_state ?? gate.arm_state;

  const send = async (command: Command) => {
    setBusy(command);
    try {
      await onCommand(gate.id, command);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className={`${cardClass} p-5 flex flex-col gap-4`} aria-label={`Gate ${gate.name}`}>
      <header className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">{gate.name}</h2>
          <p className="text-xs text-gray-500 dark:text-gray-400">{gate.location}</p>
        </div>
        <div className="flex flex-wrap gap-1 justify-end">
          {simulated && <Badge color="purple">Simulated</Badge>}
          {!gate.is_enabled && <Badge color="gray">Disabled</Badge>}
          <Badge color={gate.online ? 'green' : 'red'} title={gate.last_seen ? `Last heartbeat ${gate.last_seen}` : 'No heartbeat yet'}>
            Controller {gate.online ? 'online' : 'offline'}
          </Badge>
        </div>
      </header>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-center">
        <BarrierArm state={armState} position={gate.simulator?.position} />
        <dl className="text-sm space-y-1">
          <div className="flex justify-between gap-2">
            <dt className="text-gray-500 dark:text-gray-400">Cameras</dt>
            <dd className="text-right space-y-0.5">
              {gate.cameras.length === 0 ? <span className="text-gray-400">none</span> : gate.cameras.map((cam) => (
                <div key={cam.id} className="flex items-center justify-end gap-1">
                  <span className="text-xs text-gray-500 dark:text-gray-400">{DIRECTION_LABELS[cam.direction]}</span>
                  <span className="truncate max-w-[9rem]">{cam.name}</span>
                  <CameraStatusBadge status={cam.agent_status} />
                </div>
              ))}
            </dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-gray-500 dark:text-gray-400">Last command</dt>
            <dd className="text-right truncate" title={gate.last_command_result}>{gate.last_command_result || '—'}</dd>
          </div>
          {!simulated && (
            <div className="flex justify-between gap-2">
              <dt className="text-gray-500 dark:text-gray-400">Last heartbeat</dt>
              <dd>{gate.last_seen ? formatRelativeTime(gate.last_seen) : 'never'}</dd>
            </div>
          )}
        </dl>
      </div>

      <div className="border-t border-gray-200 dark:border-gray-700 pt-3 text-sm">
        <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">Last decision</p>
        {last ? (
          <div className="flex flex-wrap items-center gap-2">
            <DecisionBadge decision={last.decision} />
            {last.decision !== 'manual' && (
              <>
                <span className="font-mono font-semibold">{last.plate_normalized || last.plate_raw || '—'}</span>
                <ConfidenceText value={last.confidence} />
              </>
            )}
            <span className="text-gray-600 dark:text-gray-300">{reasonLabel(last)}</span>
            {last.operator && <span className="text-gray-500 dark:text-gray-400">by {last.operator}</span>}
            {last.vehicle && <span className="text-gray-500 dark:text-gray-400">· {last.vehicle.owner_name}</span>}
            {last.is_test && <Badge color="purple">test</Badge>}
            <span className="text-gray-400 ml-auto">{last.timestamp ? formatRelativeTime(last.timestamp) : ''}</span>
            <div className="w-full"><CommandBadge event={last} /></div>
          </div>
        ) : (
          <p className="text-gray-400">No decisions yet</p>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <button className={secondaryButton} disabled={busy !== null} onClick={() => send('open')}>
          {busy === 'open' ? 'Opening…' : 'Open'}
        </button>
        <button className={secondaryButton} disabled={busy !== null} onClick={() => send('close')}>
          {busy === 'close' ? 'Closing…' : 'Close'}
        </button>
        <button className={`${dangerButton} ml-auto`} disabled={busy === 'stop'} onClick={() => send('stop')} aria-label={`Stop gate ${gate.name}`}>
          STOP
        </button>
      </div>
      {gate.camera_warning && (
        <p className="text-xs text-yellow-700 dark:text-yellow-400">{gate.camera_warning}</p>
      )}
      {commandsLogged && (
        <p className="text-xs text-yellow-700 dark:text-yellow-400">
          Shadow mode: commands for this gate are recorded but not sent to the barrier.
        </p>
      )}
    </section>
  );
}
