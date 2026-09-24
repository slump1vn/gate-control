'use client';

import type { ConfigChange } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import { Badge, BadgeColor, formatDateTime } from './ui';

const ACTION: Record<string, { color: BadgeColor; label: keyof Dictionary }> = {
  create: { color: 'green', label: 'changes.created' },
  update: { color: 'blue', label: 'changes.updated' },
  delete: { color: 'red', label: 'changes.deleted' },
};

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '∅';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** Who changed what and when, for cameras and gates. Secrets appear only as "changed". */
export default function ConfigChangeList({ changes, showObject = false }: { changes: ConfigChange[]; showObject?: boolean }) {
  const { t } = useI18n();
  if (changes.length === 0) {
    return <p className="text-sm text-gray-500 dark:text-gray-400">{t('cameras.noChanges')}</p>;
  }
  return (
    <ol className="space-y-3">
      {changes.map((c) => {
        const action = ACTION[c.action];
        return (
          <li key={c.id} className="text-sm border-l-2 border-gray-200 dark:border-gray-700 pl-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge color={action?.color ?? 'gray'}>{action ? t(action.label) : c.action}</Badge>
              {showObject && <span className="font-medium">{c.object_repr}</span>}
              <span className="text-gray-500 dark:text-gray-400">{formatDateTime(c.timestamp)} · {c.user ?? t('changes.system')}</span>
            </div>
            {c.action !== 'delete' && Object.keys(c.changes).length > 0 && (
              <ul className="mt-1 text-xs text-gray-600 dark:text-gray-300 space-y-0.5">
                {Object.entries(c.changes).map(([field, change]) => (
                  <li key={field}>
                    <span className="font-mono">{field}</span>:{' '}
                    {change && typeof change === 'object' && 'new' in change
                      ? c.action === 'create'
                        ? show((change as { new: unknown }).new)
                        : <>{show((change as unknown as { old: unknown }).old)} → {show((change as { new: unknown }).new)}</>
                      : show(change)}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}
