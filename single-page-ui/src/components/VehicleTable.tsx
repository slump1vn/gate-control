import type { Vehicle } from '@/lib/gate-api';
import { REASONS } from '@/lib/gate-api';
import { Badge, formatDateTime } from './ui';

interface VehicleTableProps {
  vehicles: Vehicle[];
  onEdit: (vehicle: Vehicle) => void;
  onDeactivate: (vehicle: Vehicle) => void;
  onReactivate: (vehicle: Vehicle) => void;
}

const TYPE_LABELS: Record<string, string> = { car: 'Car', motorbike: 'Motorbike', other: 'Other' };

function AccessBadge({ vehicle }: { vehicle: Vehicle }) {
  if (vehicle.allowed_now) return <Badge color="green">Allowed</Badge>;
  return <Badge color={vehicle.access_status === 'inactive' ? 'gray' : 'yellow'}>{REASONS[vehicle.access_status] ?? vehicle.access_status}</Badge>;
}

function validity(v: Vehicle): string {
  if (!v.valid_from && !v.valid_until) return 'No limit';
  if (!v.valid_until) return `From ${formatDateTime(v.valid_from)}`;
  if (!v.valid_from) return `Until ${formatDateTime(v.valid_until)}`;
  return `${formatDateTime(v.valid_from)} → ${formatDateTime(v.valid_until)}`;
}

export default function VehicleTable({ vehicles, onEdit, onDeactivate, onReactivate }: VehicleTableProps) {
  return (
    <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-xl">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50 dark:bg-[#1a1a1a] text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
          <tr>
            <th className="px-4 py-3">Plate</th>
            <th className="px-4 py-3">Owner</th>
            <th className="px-4 py-3 hidden md:table-cell">Department</th>
            <th className="px-4 py-3 hidden lg:table-cell">Validity</th>
            <th className="px-4 py-3">Access</th>
            <th className="px-4 py-3 text-right">Actions</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
          {vehicles.map((v) => (
            <tr key={v.id} className={v.is_active ? '' : 'opacity-60'}>
              <td className="px-4 py-3">
                <div className="font-mono font-semibold">{v.plate_display}</div>
                <div className="text-xs text-gray-500 dark:text-gray-400">{v.plate_normalized} · {TYPE_LABELS[v.vehicle_type] ?? v.vehicle_type}</div>
              </td>
              <td className="px-4 py-3">
                <div>{v.owner_name}</div>
                {v.owner_phone && <div className="text-xs text-gray-500 dark:text-gray-400">{v.owner_phone}</div>}
              </td>
              <td className="px-4 py-3 hidden md:table-cell">{v.department || '—'}</td>
              <td className="px-4 py-3 hidden lg:table-cell text-xs">{validity(v)}</td>
              <td className="px-4 py-3"><AccessBadge vehicle={v} /></td>
              <td className="px-4 py-3 text-right whitespace-nowrap">
                <button onClick={() => onEdit(v)} className="text-purple-600 dark:text-purple-400 hover:underline mr-3">Edit</button>
                {v.is_active ? (
                  <button onClick={() => onDeactivate(v)} className="text-red-600 dark:text-red-400 hover:underline">Deactivate</button>
                ) : (
                  <button onClick={() => onReactivate(v)} className="text-green-600 dark:text-green-400 hover:underline">Reactivate</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
