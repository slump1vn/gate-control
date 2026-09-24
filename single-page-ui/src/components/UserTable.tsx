'use client';

import type { AppUser } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import { Badge, formatDateTime } from './ui';

interface UserTableProps {
  users: AppUser[];
  currentUsername: string | null;
  onEdit: (user: AppUser) => void;
  onDeactivate: (user: AppUser) => void;
  onReactivate: (user: AppUser) => void;
}

export default function UserTable({ users, currentUsername, onEdit, onDeactivate, onReactivate }: UserTableProps) {
  const { t } = useI18n();

  return (
    <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-xl">
      <table className="min-w-full text-sm">
        <thead className="bg-gray-50 dark:bg-[#1a1a1a] text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
          <tr>
            <th className="px-4 py-3">{t('users.username')}</th>
            <th className="px-4 py-3 hidden md:table-cell">{t('users.email')}</th>
            <th className="px-4 py-3">{t('users.role')}</th>
            <th className="px-4 py-3">{t('users.status')}</th>
            <th className="px-4 py-3 hidden lg:table-cell">{t('users.lastLogin')}</th>
            <th className="px-4 py-3 text-right">{t('vehicles.actions')}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
          {users.map((u) => {
            const isSelf = u.username === currentUsername;
            return (
              <tr key={u.id} className={u.is_active ? '' : 'opacity-60'}>
                <td className="px-4 py-3">
                  <div className="font-medium">{u.username}</div>
                  {isSelf && <div className="text-xs text-gray-500 dark:text-gray-400">{t('users.you')}</div>}
                </td>
                <td className="px-4 py-3 hidden md:table-cell">{u.email || '—'}</td>
                <td className="px-4 py-3">
                  <Badge color={u.role === 'gate_admin' ? 'purple' : 'blue'}>
                    {t(u.role === 'gate_admin' ? 'users.roleAdmin' : 'users.roleOperator')}
                  </Badge>
                </td>
                <td className="px-4 py-3">
                  <Badge color={u.is_active ? 'green' : 'gray'}>{t(u.is_active ? 'users.active' : 'users.inactive')}</Badge>
                </td>
                <td className="px-4 py-3 hidden lg:table-cell text-xs">
                  {u.last_login ? formatDateTime(u.last_login) : t('common.never')}
                </td>
                <td className="px-4 py-3 text-right whitespace-nowrap">
                  <button onClick={() => onEdit(u)} className="text-purple-600 dark:text-purple-400 hover:underline mr-3">{t('common.edit')}</button>
                  {u.is_active ? (
                    <button onClick={() => onDeactivate(u)} disabled={isSelf}
                      className="text-red-600 dark:text-red-400 hover:underline disabled:opacity-40 disabled:pointer-events-none">
                      {t('users.deactivate')}
                    </button>
                  ) : (
                    <button onClick={() => onReactivate(u)} className="text-green-600 dark:text-green-400 hover:underline">{t('users.reactivate')}</button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
