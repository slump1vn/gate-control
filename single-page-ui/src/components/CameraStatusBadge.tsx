'use client';

import { useI18n } from './I18nContext';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import { Badge, BadgeColor } from './ui';

const STATUS: Record<string, { color: BadgeColor; label: keyof Dictionary; hint: keyof Dictionary }> = {
  streaming: { color: 'green', label: 'camera.streaming', hint: 'camera.streamingHint' },
  reconnecting: { color: 'yellow', label: 'camera.reconnecting', hint: 'camera.reconnectingHint' },
  auth_failed: { color: 'red', label: 'camera.authFailed', hint: 'camera.authFailedHint' },
  unreachable: { color: 'red', label: 'camera.unreachable', hint: 'camera.unreachableHint' },
  disabled: { color: 'gray', label: 'camera.disabled', hint: 'camera.disabledHint' },
};

/** Camera status as last reported by the gate agent. */
export default function CameraStatusBadge({ status }: { status: string | null | undefined }) {
  const { t } = useI18n();
  if (!status) return <Badge color="gray" title={t('camera.noReportHint')}>{t('camera.noReport')}</Badge>;
  const s = STATUS[status];
  if (!s) return <Badge color="gray">{status}</Badge>;
  return <Badge color={s.color} title={t(s.hint)}>{t(s.label)}</Badge>;
}
