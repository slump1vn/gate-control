'use client';

import type { PlateAlert } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import { Badge, formatDateTime } from './ui';

interface PlateAlertTableProps {
  alerts: PlateAlert[];
  onEdit: (alert: PlateAlert) => void;
  onToggle: (alert: PlateAlert) => void;
  onDelete: (alert: PlateAlert) => void;
}

const DIRECTION_KEYS = { both: 'alerts.dirBoth', in: 'alerts.dirIn', out: 'alerts.dirOut' } as const;
const STATUS = {
  sent: { key: 'alerts.statusSent', color: 'green' },
  failed: { key: 'alerts.statusFailed', color: 'red' },
  pending: { key: 'alerts.statusPending', color: 'yellow' },
} as const;

export default function PlateAlertTable({ alerts, onEdit, onToggle, onDelete }: PlateAlertTableProps) {
  const { t } = useI18n();

  return (
    <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-xl">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50 dark:bg-[#1a1a1a] text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
          <tr>
            <th className="px-4 py-3">{t('alerts.plate')}</th>
            <th className="px-4 py-3 hidden md:table-cell">{t('alerts.directions')}</th>
            <th className="px-4 py-3 hidden lg:table-cell">{t('alerts.chatIds')}</th>
            <th className="px-4 py-3">{t('alerts.status')}</th>
            <th className="px-4 py-3 hidden md:table-cell">{t('alerts.lastSent')}</th>
            <th className="px-4 py-3 text-right">{t('vehicles.actions')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
          {alerts.map((a) => {
            const last = a.last_delivery;
            return (
              <tr key={a.id} className={a.is_active ? '' : 'opacity-60'}>
                <td className="px-4 py-3">
                  <div className="font-mono font-medium">{a.plate_display}</div>
                  {a.label && <div className="text-xs text-gray-500 dark:text-gray-400">{a.label}</div>}
                </td>
                <td className="px-4 py-3 hidden md:table-cell">{t(DIRECTION_KEYS[a.directions])}</td>
                <td className="px-4 py-3 hidden lg:table-cell font-mono text-xs break-all">
                  {a.chat_ids || <span className="font-sans text-gray-500 dark:text-gray-400">{t('alerts.chatDefault')}</span>}
                </td>
                <td className="px-4 py-3">
                  <Badge color={a.is_active ? 'green' : 'gray'}>{t(a.is_active ? 'alerts.active' : 'alerts.paused')}</Badge>
                </td>
                <td className="px-4 py-3 hidden md:table-cell text-xs">
                  {last ? (
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <Badge color={STATUS[last.status].color} title={last.error || undefined}>{t(STATUS[last.status].key)}</Badge>
                        {last.at && <span>{formatDateTime(last.at)}</span>}
                      </div>
                      {last.error && <div className="text-red-600 dark:text-red-400 break-words">{last.error}</div>}
                    </div>
                  ) : t('common.never')}
                </td>
                <td className="px-4 py-3 text-right whitespace-nowrap">
                  <button onClick={() => onEdit(a)} className="text-purple-600 dark:text-purple-400 hover:underline mr-3">{t('common.edit')}</button>
                  <button onClick={() => onToggle(a)} className="text-gray-600 dark:text-gray-300 hover:underline mr-3">
                    {t(a.is_active ? 'alerts.pause' : 'alerts.resume')}
                  </button>
                  <button onClick={() => onDelete(a)} className="text-red-600 dark:text-red-400 hover:underline">{t('common.delete')}</button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
