import type { ArmState } from '@/lib/gate-api';

const ARM_LABELS: Record<ArmState, string> = {
  up: 'Open',
  down: 'Closed',
  moving: 'Moving',
  stopped: 'Stopped',
  unknown: 'Unknown',
};

/** Arm angle from the simulator's position (0 closed … 1 open) or the reported state. */
function armPosition(state: ArmState | null, position?: number | null): number | null {
  if (typeof position === 'number') return Math.min(1, Math.max(0, position));
  if (state === 'up') return 1;
  if (state === 'down') return 0;
  if (state === 'moving' || state === 'stopped') return 0.5;
  return null;
}

export default function BarrierArm({ state, position, size = 160 }: { state: ArmState | null; position?: number | null; size?: number }) {
  const pos = armPosition(state, position);
  const angle = -80 * (pos ?? 0);
  const label = state ? ARM_LABELS[state] : 'No feedback';
  return (
    <figure className="flex flex-col items-center" aria-label={`Barrier arm: ${label}`}>
      <svg width={size} height={size * 0.6} viewBox="0 0 200 120" role="img" aria-hidden="true">
        <rect x="0" y="112" width="200" height="4" rx="2" className="fill-gray-300 dark:fill-gray-600" />
        <rect x="20" y="62" width="26" height="50" rx="4" className="fill-gray-500 dark:fill-gray-400" />
        <g transform={`rotate(${angle} 33 70)`} style={{ transition: 'transform 0.45s linear' }}>
          <rect x="33" y="65" width="160" height="10" rx="5" fill={pos === null ? '#9ca3af' : '#ef4444'} />
          {[0, 1, 2, 3].map((i) => (
            <rect key={i} x={53 + i * 38} y="65" width="16" height="10" fill="#ffffff" opacity={pos === null ? 0.4 : 0.9} />
          ))}
        </g>
        <circle cx="33" cy="70" r="6" className="fill-gray-700 dark:fill-gray-200" />
      </svg>
      <figcaption className="text-sm font-medium mt-1">{label}</figcaption>
    </figure>
  );
}
