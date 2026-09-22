import { Badge, BadgeColor } from './ui';

const STATUS: Record<string, { color: BadgeColor; label: string; hint: string }> = {
  streaming: { color: 'green', label: 'Streaming', hint: 'The gate agent is receiving frames.' },
  reconnecting: { color: 'yellow', label: 'Reconnecting', hint: 'The agent lost the camera and is retrying.' },
  auth_failed: { color: 'red', label: 'Auth failed', hint: 'The camera rejected the username or password.' },
  unreachable: { color: 'red', label: 'Unreachable', hint: 'The agent cannot connect to the camera.' },
  disabled: { color: 'gray', label: 'Disabled', hint: 'The camera is disabled in its settings.' },
};

/** Camera status as last reported by the gate agent. */
export default function CameraStatusBadge({ status }: { status: string | null | undefined }) {
  if (!status) return <Badge color="gray" title="The gate agent has not reported this camera yet.">No report</Badge>;
  const s = STATUS[status] ?? { color: 'gray' as BadgeColor, label: status, hint: '' };
  return <Badge color={s.color} title={s.hint}>{s.label}</Badge>;
}
