'use client';

import type { AccessEvent } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import { Badge, BadgeColor } from './ui';

const DECISION_COLORS: Record<string, BadgeColor> = {
  granted: 'green',
  denied: 'red',
  manual: 'blue',
};

export function DecisionBadge({ decision }: { decision: string }) {
  const { t } = useI18n();
  return <Badge color={DECISION_COLORS[decision] ?? 'gray'}>{t(`decision.${decision}` as keyof Dictionary)}</Badge>;
}

/** The service also sends its own wording; the dictionary wins so the page is in one language. */
export function useReasonLabel() {
  const { t } = useI18n();
  return (event: Pick<AccessEvent, 'reason' | 'reason_display'>) => {
    const key = `reason.${event.reason}` as keyof Dictionary;
    const translated = t(key);
    return translated === key ? (event.reason_display || event.reason) : translated;
  };
}

const NOT_SENT = new Set([
  'not_sent_shadow_mode', 'not_sent_test', 'not_sent_agent_mode',
  'not_sent_cli_real_controller', 'expired',
]);

/**
 * Whether the command reached the barrier, in words. command_result is empty
 * while queued, "dispatched" once the agent took it, then the controller's
 * reply (sent) or the failure (not sent).
 */
export function CommandBadge({ event }: { event: Pick<AccessEvent, 'command' | 'command_sent' | 'command_result'> }) {
  const { t } = useI18n();
  if (!event.command) return <span className="text-gray-400">—</span>;
  const result = event.command_result;
  let color: BadgeColor;
  let label: string;
  if (event.command_sent) {
    color = 'green';
    label = result ? t('command.sentWith', { result }) : t('command.sent');
  } else if (!result || result === 'dispatched') {
    color = 'yellow';
    label = t(result ? 'command.dispatched' : 'command.queued');
  } else if (NOT_SENT.has(result)) {
    color = 'gray';
    label = t(`command.${result}` as keyof Dictionary);
  } else {
    color = 'red';
    label = t('command.failed', { result });
  }
  return <Badge color={color} title={result}>{event.command} — {label}</Badge>;
}

export function ConfidenceText({ value }: { value: number | null }) {
  if (value === null || value === undefined) return <span className="text-gray-400">—</span>;
  const pct = Math.round(value * 100);
  const cls = value >= 0.8 ? 'text-green-600 dark:text-green-400' : value >= 0.5 ? 'text-yellow-600 dark:text-yellow-400' : 'text-red-600 dark:text-red-400';
  return <span className={cls}>{pct}%</span>;
}
