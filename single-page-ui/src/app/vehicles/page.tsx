'use client';

import { FormEvent, useCallback, useState } from 'react';
import { createVehicle, deactivateVehicle, getVehicles, updateVehicle } from '@/lib/gate-api';
import type { Vehicle, VehicleInput } from '@/lib/gate-api';
import { usePolling } from '@/hooks/usePolling';
import RequireRole from '@/components/RequireRole';
import VehicleForm from '@/components/VehicleForm';
import VehicleTable from '@/components/VehicleTable';
import Pagination from '@/components/Pagination';
import Spinner from '@/components/Spinner';
import { Alert, Modal, PageHeader, inputClass, primaryButton, secondaryButton } from '@/components/ui';

const PAGE_SIZE = 25;

function VehiclesContent() {
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
      setError(err instanceof Error ? err.message : 'Failed to load vehicles');
    } finally {
      setLoading(false);
    }
  }, [q, active, page]);

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
      setNotice(`Added ${v.plate_display}.`);
    } else if (editing) {
      const v = await updateVehicle(editing.id, data);
      setNotice(`Saved ${v.plate_display}.`);
    }
    setEditing(null);
    reload();
  };

  const deactivate = async (v: Vehicle) => {
    if (!window.confirm(`Deactivate ${v.plate_display} (${v.owner_name})? The barrier will no longer open for it.`)) return;
    try {
      await deactivateVehicle(v.id);
      setNotice(`${v.plate_display} deactivated.`);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Deactivation failed');
    }
  };

  const reactivate = async (v: Vehicle) => {
    try {
      await updateVehicle(v.id, { is_active: true });
      setNotice(`${v.plate_display} reactivated.`);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Reactivation failed');
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title="Registered vehicles">
        <button className={primaryButton} onClick={() => setEditing('new')}>Add vehicle</button>
      </PageHeader>

      <form onSubmit={submitSearch} className="flex flex-col sm:flex-row gap-2 mb-4">
        <input
          className={`${inputClass} sm:w-80`}
          placeholder="Plate, owner or department"
          aria-label="Search vehicles"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select className={`${inputClass} sm:w-40`} aria-label="Status" value={active}
          onChange={(e) => { setActive(e.target.value as typeof active); setPage(1); }}>
          <option value="true">Active</option>
          <option value="false">Inactive</option>
          <option value="">All</option>
        </select>
        <button type="submit" className={secondaryButton}>Search</button>
      </form>

      <div className="space-y-3 mb-4">
        {error && <Alert>{error}</Alert>}
        {notice && <Alert kind="success">{notice}</Alert>}
      </div>

      {loading ? (
        <Spinner />
      ) : vehicles.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">No vehicles found</div>
      ) : (
        <>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">{count} vehicle{count !== 1 ? 's' : ''}</p>
          <VehicleTable vehicles={vehicles} onEdit={setEditing} onDeactivate={deactivate} onReactivate={reactivate} />
          <Pagination currentPage={page} totalPages={Math.ceil(count / PAGE_SIZE)} onPageChange={setPage} />
        </>
      )}

      {editing && (
        <Modal title={editing === 'new' ? 'Add vehicle' : `Edit ${editing.plate_display}`} onClose={() => setEditing(null)}>
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
