'use client';

import { FormEvent, useCallback, useState } from 'react';
import { createVehicle, deactivateVehicle, getVehicles, updateVehicle } from '@/lib/gate-api';
import type { Vehicle, VehicleInput } from '@/lib/gate-api';
import { usePolling } from '@/hooks/usePolling';
import RequireRole from '@/components/RequireRole';
import { useI18n } from '@/components/I18nContext';
import VehicleForm from '@/components/VehicleForm';
import VehicleTable from '@/components/VehicleTable';
import Pagination from '@/components/Pagination';
import Spinner from '@/components/Spinner';
import { Alert, Modal, PageHeader, inputClass, primaryButton, secondaryButton } from '@/components/ui';

const PAGE_SIZE = 25;

function VehiclesContent() {
  const { t } = useI18n();
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  const [count, setCount] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [q, setQ] = useState('');
  const [active, setActive] = useState<'' | 'true' | 'false'>('true');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<Vehicle | 'new' | null>(null);
  const [version, setVersion] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await getVehicles({ q, is_active: active || undefined, page, page_size: PAGE_SIZE });
      setVehicles(data.results);
      setCount(data.count);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('vehicles.loadFailed'));
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

  const save = async (data: VehicleInput) => {
    if (editing === 'new') {
      const v = await createVehicle(data);
      setNotice(t('vehicles.added', { plate: v.plate_display }));
    } else if (editing) {
      const v = await updateVehicle(editing.id, data);
      setNotice(t('vehicles.saved', { plate: v.plate_display }));
    }
    setEditing(null);
    reload();
  };

  const deactivate = async (v: Vehicle) => {
    if (!window.confirm(t('vehicles.confirmDeactivate', { plate: v.plate_display, owner: v.owner_name }))) return;
    try {
      await deactivateVehicle(v.id);
      setNotice(t('vehicles.deactivated', { plate: v.plate_display }));
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('vehicles.saveFailed'));
    }
  };

  const reactivate = async (v: Vehicle) => {
    try {
      await updateVehicle(v.id, { is_active: true });
      setNotice(t('vehicles.reactivated', { plate: v.plate_display }));
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('vehicles.saveFailed'));
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title={t('vehicles.title')}>
        <button className={primaryButton} onClick={() => setEditing('new')}>{t('vehicles.add')}</button>
      </PageHeader>

      <form onSubmit={submitSearch} className="flex flex-col sm:flex-row gap-2 mb-4">
        <input
          className={`${inputClass} sm:w-80`}
          placeholder={t('vehicles.searchPlaceholder')}
          aria-label={t('vehicles.searchLabel')}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={`${inputClass} sm:w-40`} aria-label={t('vehicles.statusLabel')} value={active}
          onChange={(e) => { setActive(e.target.value as typeof active); setPage(1); }}>
          <option value="true">{t('vehicles.active')}</option>
          <option value="false">{t('vehicles.inactive')}</option>
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
      ) : vehicles.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">{t('vehicles.none')}</div>
      ) : (
        <>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">{t('vehicles.count', { n: count })}</p>
          <VehicleTable vehicles={vehicles} onEdit={setEditing} onDeactivate={deactivate} onReactivate={reactivate} />
          <Pagination currentPage={page} totalPages={Math.ceil(count / PAGE_SIZE)} onPageChange={setPage} />
        </>
      )}

      {editing && (
        <Modal
          title={editing === 'new' ? t('vehicles.add') : t('vehicles.edit', { plate: editing.plate_display })}
          onClose={() => setEditing(null)}
        >
          <VehicleForm vehicle={editing === 'new' ? null : editing} onSubmit={save} onCancel={() => setEditing(null)} />
        </Modal>
      )}
    </div>
  );
}

export default function VehiclesPage() {
  return (
    <RequireRole role="gate_operator">
      <VehiclesContent />
    </RequireRole>
  );
}
