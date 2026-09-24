'use client';

import { FormEvent, useCallback, useState } from 'react';
import { createUser, deactivateUser, getUsers, updateUser } from '@/lib/gate-api';
import type { AppUser, UserInput } from '@/lib/gate-api';
import { usePolling } from '@/hooks/usePolling';
import { useAuth } from '@/components/AuthContext';
import RequireRole from '@/components/RequireRole';
import { useI18n } from '@/components/I18nContext';
import UserForm from '@/components/UserForm';
import UserTable from '@/components/UserTable';
import Pagination from '@/components/Pagination';
import Spinner from '@/components/Spinner';
import { Alert, Modal, PageHeader, inputClass, primaryButton, secondaryButton } from '@/components/ui';

const PAGE_SIZE = 25;

function UsersContent() {
  const { t } = useI18n();
  const { username: currentUsername } = useAuth();
  const [users, setUsers] = useState<AppUser[]>([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const [active, setActive] = useState<'' | 'true' | 'false'>('true');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<AppUser | 'new' | null>(null);
  const [version, setVersion] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getUsers({ q, is_active: active || undefined, page, page_size: PAGE_SIZE });
      setUsers(data.results);
      setCount(data.count);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('users.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [q, active, page, t]);

  usePolling(load, null, `${q}|${active}|${page}|${version}`);
  const reload = () => setVersion((v) => v + 1);

  const submitSearch = (e: FormEvent) => {
    e.preventDefault();
    setQ(search.trim());
    setPage(1);
  };

  const save = async (data: UserInput) => {
    if (editing === 'new') {
      const u = await createUser(data);
      setNotice(t('users.added', { username: u.username }));
    } else if (editing) {
      const u = await updateUser(editing.id, data);
      setNotice(t('users.saved', { username: u.username }));
    }
    setEditing(null);
    reload();
  };

  const deactivate = async (u: AppUser) => {
    if (!window.confirm(t('users.confirmDeactivate', { username: u.username }))) return;
    try {
      await deactivateUser(u.id);
      setNotice(t('users.deactivated', { username: u.username }));
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('users.saveFailed'));
    }
  };

  const reactivate = async (u: AppUser) => {
    try {
      await updateUser(u.id, { is_active: true });
      setNotice(t('users.reactivated', { username: u.username }));
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('users.saveFailed'));
    }
  };

  const editingUser = editing === 'new' ? null : editing;
  const isSelf = !!editingUser && editingUser.username === currentUsername;

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title={t('users.title')}>
        <button className={primaryButton} onClick={() => setEditing('new')}>{t('users.add')}</button>
      </PageHeader>

      <form onSubmit={submitSearch} className="flex flex-col sm:flex-row gap-2 mb-4">
        <input
          className={`${inputClass} sm:w-80`}
          placeholder={t('users.searchPlaceholder')}
          aria-label={t('users.searchPlaceholder')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={`${inputClass} sm:w-40`} aria-label={t('users.status')} value={active}
          onChange={(e) => { setActive(e.target.value as typeof active); setPage(1); }}>
          <option value="true">{t('users.active')}</option>
          <option value="false">{t('users.inactive')}</option>
          <option value="">{t('vehicles.all')}</option>
        </select>
        <button type="submit" className={secondaryButton}>{t('common.search')}</button>
      </form>

      <div className="space-y-3 mb-4">
        {error && <Alert>{error}</Alert>}
        {notice && <Alert kind="success">{notice}</Alert>}
      </div>

      {loading ? (
        <Spinner />
      ) : users.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">{t('users.none')}</div>
      ) : (
        <>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">{t('users.count', { n: count })}</p>
          <UserTable users={users} currentUsername={currentUsername} onEdit={setEditing} onDeactivate={deactivate} onReactivate={reactivate} />
          <Pagination currentPage={page} totalPages={Math.ceil(count / PAGE_SIZE)} onPageChange={setPage} />
        </>
      )}

      {editing && (
        <Modal
          title={editing === 'new' ? t('users.add') : t('users.edit', { username: editingUser!.username })}
          onClose={() => setEditing(null)}
        >
          <UserForm user={editingUser} isSelf={isSelf} onSubmit={save} onCancel={() => setEditing(null)} />
        </Modal>
      )}
    </div>
  );
}

export default function UsersPage() {
  return (
    <RequireRole role="gate_admin">
      <UsersContent />
    </RequireRole>
  );
}
