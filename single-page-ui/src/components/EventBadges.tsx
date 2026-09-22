import type { AccessEvent } from '@/lib/gate-api';
import { REASONS } from '@/lib/gate-api';
import { Badge, BadgeColor } from './ui';

const DECISION_COLORS: Record<string, BadgeColor> = {
  granted: 'green',
  denied: 'red',
  manual: 'blue',
};

export function DecisionBadge({ decision }: { decision: string }) {
  return <Badge color={DECISION_COLORS[decision] ?? 'gray'}>{decision.charAt(0).toUpperCase() + decision.slice(1)}</Badge>;
}

export function reasonLabel(event: Pick<AccessEvent, 'reason' | 'reason_display'>): string {
  return event.reason_display || REASONS[event.reason] || event.reason;
}

const NOT_SENT_LABELS: Record<string, string> = {
  not_sent_shadow_mode: 'not sent (shadow mode)',
  not_sent_test: 'not sent (test)',
  not_sent_agent_mode: 'not sent (agent in shadow)',
  not_sent_cli_real_controller: 'not sent (CLI)',
  expired: 'expired, not sent',
};

/**
 * Whether the command reached the barrier, in words. command_result is empty
 * while queued, "dispatched" once the agent took it, then the controller's
 * reply (sent) or the failure (not sent).
 */
export function CommandBadge({ event }: { event: Pick<AccessEvent, 'command' | 'command_sent' | 'command_result'> }) {
  if (!event.command) return <span className="text-gray-400">—</span>;
  const result = event.command_result;
  let color: BadgeColor;
  let label: string;
  if (event.command_sent) {
    color = 'green';
    label = result ? `sent (${result})` : 'sent';
  } else if (!result || result === 'dispatched') {
    color = 'yellow';
    label = result ? 'dispatched' : 'queued';
  } else if (NOT_SENT_LABELS[result]) {
    color = 'gray';
    label = NOT_SENT_LABELS[result];
  } else {
    color = 'red';
    label = `failed: ${result}`;
  }
  return <Badge color={color} title={result}>{event.command} — {label}</Badge>;
}

export function ConfidenceText({ value }: { value: number | null }) {
  if (value === null || value === undefined) return <span className="text-gray-400">—</span>;
  const pct = Math.round(value * 100);
  const cls = value >= 0.8 ? 'text-green-600 dark:text-green-400' : value >= 0.5 ? 'text-yellow-600 dark:text-yellow-400' : 'text-red-600 dark:text-red-400';
  return <span className={cls}>{pct}%</span>;
}
