import type { TriggerReadout as Readout } from '@/lib/gate-api';

const STATE_LABELS: Record<string, string> = {
  idle: 'Lane empty',
  motion: 'Something moving',
  occupied: 'Vehicle in the zone',
};

function Score({ label, value, threshold }: { label: string; value: number; threshold: number }) {
  const reached = value >= threshold;
  const width = Math.min(100, (value / Math.max(threshold * 1.5, 0.0001)) * 100);
  return (
    <div className="flex items-center gap-2">
      <span className="w-16 text-gray-500 dark:text-gray-400">{label}</span>
      <span className="relative flex-1 h-1.5 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden">
        <span className={`absolute inset-y-0 left-0 ${reached ? 'bg-green-500' : 'bg-gray-400'}`} style={{ width: `${width}%` }} />
        {/* Where the score has to reach before the agent acts */}
        <span className="absolute inset-y-0 w-px bg-yellow-500" style={{ left: '66.7%' }} />
      </span>
      <span className={`tabular-nums ${reached ? 'text-green-600 dark:text-green-400' : 'text-gray-500 dark:text-gray-400'}`}>
        {value.toFixed(3)}
      </span>
      <span className="text-gray-400 tabular-nums">/ {threshold.toFixed(3)}</span>
    </div>
  );
}

/**
 * What the agent's trigger is seeing on this camera. When a vehicle arrives and
 * no event appears, this says whether it was noticed at all: presence below its
 * threshold means the read zone is wrong or the vehicle fills too little of it.
 */
export default function TriggerReadout({ readout }: { readout: Readout | null | undefined }) {
  if (!readout) {
    return <p className="text-xs text-gray-500 dark:text-gray-400">The agent has not reported this camera yet.</p>;
  }
  return (
    <div className="text-xs space-y-1">
      <div className="flex flex-wrap items-center gap-2 text-gray-600 dark:text-gray-300">
        <span>{STATE_LABELS[readout.state ?? ''] ?? 'Unknown'}</span>
        {typeof readout.fps === 'number' && (
          <span className="text-gray-500 dark:text-gray-400">· {readout.fps.toFixed(1)} frames/s</span>
        )}
      </div>
      {typeof readout.motion === 'number' && typeof readout.motion_threshold === 'number' && (
        <Score label="Motion" value={readout.motion} threshold={readout.motion_threshold} />
      )}
      {typeof readout.presence === 'number' && typeof readout.presence_threshold === 'number' && (
        <Score label="Presence" value={readout.presence} threshold={readout.presence_threshold} />
      )}
    </div>
  );
}
