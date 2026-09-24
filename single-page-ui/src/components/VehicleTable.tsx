'use client';

import type { Vehicle } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import { Badge, formatDateTime } from './ui';

interface VehicleTableProps {
  vehicles: Vehicle[];
  onEdit: (vehicle: Vehicle) => void;
  onDeactivate: (vehicle: Vehicle) => void;
  onReactivate: (vehicle: Vehicle) => void;
}

const TYPE_LABELS: Record<string, keyof Dictionary> = {
  car: 'vehicleForm.car', motorbike: 'vehicleForm.motorbike', other: 'vehicleForm.other',
};

function AccessBadge({ vehicle }: { vehicle: Vehicle }) {
  const { t } = useI18n();
  if (vehicle.allowed_now) return <Badge color="green">{t('vehicles.allowed')}</Badge>;
  return (
    <Badge color={vehicle.access_status === 'inactive' ? 'gray' : 'yellow'}>
      {t(`reason.${vehicle.access_status}` as keyof Dictionary)}
    </Badge>
  );
}

export default function VehicleTable({ vehicles, onEdit, onDeactivate, onReactivate }: VehicleTableProps) {
  const { t } = useI18n();

  const validity = (v: Vehicle): string => {
    if (!v.valid_from && !v.valid_until) return t('vehicles.noLimit');
    if (!v.valid_until) return t('vehicles.from', { date: formatDateTime(v.valid_from) });
    if (!v.valid_from) return t('vehicles.until', { date: formatDateTime(v.valid_until) });
    return `${formatDateTime(v.valid_from)} → ${formatDateTime(v.valid_until)}`;
  };

  return (
    <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-xl">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50 dark:bg-[#1a1a1a] text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
          <tr>
            <th className="px-4 py-3">{t('vehicles.plate')}</th>
            <th className="px-4 py-3">{t('vehicles.owner')}</th>
            <th className="px-4 py-3 hidden md:table-cell">{t('vehicles.department')}</th>
            <th className="px-4 py-3 hidden lg:table-cell">{t('vehicles.validity')}</th>
            <th className="px-4 py-3">{t('vehicles.access')}</th>
            <th className="px-4 py-3 text-right">{t('vehicles.actions')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
          {vehicles.map((v) => (
            <tr key={v.id} className={v.is_active ? '' : 'opacity-60'}>
              <td className="px-4 py-3">
                <div className="font-mono font-semibold">{v.plate_display}</div>
                <div className="text-xs text-gray-500 dark:text-gray-400">{v.plate_normalized} · {t(TYPE_LABELS[v.vehicle_type] ?? 'vehicleForm.other')}</div>
              </td>
              <td className="px-4 py-3">
                <div>{v.owner_name}</div>
                {v.owner_phone && <div className="text-xs text-gray-500 dark:text-gray-400">{v.owner_phone}</div>}
              </td>
              <td className="px-4 py-3 hidden md:table-cell">{v.department || '—'}</td>
              <td className="px-4 py-3 hidden lg:table-cell text-xs">{validity(v)}</td>
              <td className="px-4 py-3"><AccessBadge vehicle={v} /></td>
              <td className="px-4 py-3 text-right whitespace-nowrap">
                <button onClick={() => onEdit(v)} className="text-purple-600 dark:text-purple-400 hover:underline mr-3">{t('common.edit')}</button>
                {v.is_active ? (
                  <button onClick={() => onDeactivate(v)} className="text-red-600 dark:text-red-400 hover:underline">{t('vehicles.deactivate')}</button>
                ) : (
                  <button onClick={() => onReactivate(v)} className="text-green-600 dark:text-green-400 hover:underline">{t('vehicles.reactivate')}</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
