import type { ConfigChange } from '@/lib/gate-api';
import { Badge, BadgeColor, formatDateTime } from './ui';

const ACTION: Record<string, { color: BadgeColor; label: string }> = {
  create: { color: 'green', label: 'Created' },
  update: { color: 'blue', label: 'Updated' },
  delete: { color: 'red', label: 'Deleted' },
};

function show(value: unknown): string {
  if (value === null || value === undefined || value === '') return '∅';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

/** Who changed what and when, for cameras and gates. Secrets appear only as "changed". */
export default function ConfigChangeList({ changes, showObject = false }: { changes: ConfigChange[]; showObject?: boolean }) {
  if (changes.length === 0) {
    return <p className="text-sm text-gray-500 dark:text-gray-400">No changes recorded.</p>;
  }
  return (
    <ol className="space-y-3">
      {changes.map((c) => {
        const action = ACTION[c.action] ?? { color: 'gray' as BadgeColor, label: c.action };
        return (
          <li key={c.id} className="text-sm border-l-2 border-gray-200 dark:border-gray-700 pl-3">
            <div className="flex flex-wrap items-center gap-2">
              <Badge color={action.color}>{action.label}</Badge>
              {showObject && <span className="font-medium">{c.object_repr}</span>}
              <span className="text-gray-500 dark:text-gray-400">{formatDateTime(c.timestamp)} · {c.user ?? 'system'}</span>
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
