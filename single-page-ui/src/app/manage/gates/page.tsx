'use client';

import { useCallback, useEffect, useState } from 'react';
import { usePolling } from '@/hooks/usePolling';
import Link from 'next/link';
import { getApiBase } from '@/lib/api';
import { createGateDevice, deleteGateDevice, getCameras, getGateDevices, updateGateDevice } from '@/lib/gate-api';
import type { GateDevice, GateDeviceInput } from '@/lib/gate-api';
import RequireRole from '@/components/RequireRole';
import GateDeviceForm from '@/components/GateDeviceForm';
import Spinner from '@/components/Spinner';
import { Alert, Badge, Modal, PageHeader, formatDateTime, primaryButton } from '@/components/ui';

function GatesContent() {
  const [gates, setGates] = useState<GateDevice[] | null>(null);
  const [cameras, setCameras] = useState<{ id: number; name: string }[]>([]);
  const [apiBase, setApiBase] = useState('');
  const [editing, setEditing] = useState<GateDevice | 'new' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [g, c] = await Promise.all([getGateDevices(), getCameras()]);
      setGates(g);
      setCameras(c.map(({ id, name }) => ({ id, name })));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load gates');
    }
  }, []);

  usePolling(load, 15000);
  useEffect(() => { getApiBase().then(setApiBase); }, []);

  const save = async (data: GateDeviceInput) => {
    const saved = editing === 'new' || !editing ? await createGateDevice(data) : await updateGateDevice(editing.id, data);
    setNotice(`Saved ${saved.name}.`);
    setEditing(null);
    load();
  };

  const remove = async (gate: GateDevice) => {
    if (!window.confirm(`Delete gate "${gate.name}"? Its access events are kept.`)) return;
    try {
      await deleteGateDevice(gate.id);
      setNotice(`Deleted ${gate.name}.`);
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed');
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title="Gates">
        <button className={primaryButton} onClick={() => setEditing('new')}>Add gate</button>
      </PageHeader>

      <div className="space-y-3 mb-4">
        {error && <Alert>{error}</Alert>}
        {notice && <Alert kind="success">{notice}</Alert>}
      </div>

      {!gates ? (
        error ? null : <Spinner />
      ) : gates.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">
          No gates yet. Add one with a <strong>simulated barrier</strong> to test recognition before the ESP32 is installed.
        </div>
      ) : (
        <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-xl">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 dark:bg-[#1a1a1a] text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
              <tr>
                <th className="px-4 py-3">Gate</th>
                <th className="px-4 py-3">Camera</th>
                <th className="px-4 py-3">Controller</th>
                <th className="px-4 py-3">State</th>
                <th className="px-4 py-3 text-right"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {gates.map((g) => (
                <tr key={g.id}>
                  <td className="px-4 py-3">
                    <div className="font-medium">{g.name}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400">{g.direction === 'in' ? 'Entry' : 'Exit'}{g.location ? ` · ${g.location}` : ''}</div>
                  </td>
                  <td className="px-4 py-3">
                    {g.camera
                      ? <Link href={`/manage/cameras/${g.camera.id}`} className="text-purple-600 dark:text-purple-400 hover:underline">{g.camera.name}</Link>
                      : <span className="text-gray-400">none</span>}
                  </td>
                  <td className="px-4 py-3">
                    {g.controller_type === 'simulator'
                      ? <Badge color="purple">Simulated</Badge>
                      : <><div className="font-mono text-xs">{g.controller_url || '—'}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          {g.controller_token_set ? 'token set' : <span className="text-red-600 dark:text-red-400">no token</span>}
                          {g.firmware_version && ` · fw ${g.firmware_version}`}
                        </div></>}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {g.is_enabled ? <Badge color="green">Enabled</Badge> : <Badge>Disabled</Badge>}
                      <Badge color={g.online ? 'green' : 'red'} title={g.last_seen ? `Last heartbeat ${formatDateTime(g.last_seen)}` : 'No heartbeat yet'}>
                        {g.online ? 'Online' : 'Offline'}
                      </Badge>
                      {g.has_safety_input && <Badge color="blue">Safety sensor</Badge>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap space-x-3">
                    <a href={`${apiBase}/admin/lpr_app/gatedevice/${g.id}/test/`} target="_blank" rel="noopener noreferrer"
                      className="text-purple-600 dark:text-purple-400 hover:underline" title="Upload photos and run them through recognition (Django admin)">
                      Test recognition
                    </a>
                    <button onClick={() => setEditing(g)} className="text-purple-600 dark:text-purple-400 hover:underline">Edit</button>
                    <button onClick={() => remove(g)} className="text-red-600 dark:text-red-400 hover:underline">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <Modal title={editing === 'new' ? 'Add gate' : `Edit ${editing.name}`} onClose={() => setEditing(null)}>
          <GateDeviceForm gate={editing === 'new' ? null : editing} cameras={cameras} onSubmit={save} onCancel={() => setEditing(null)} />
        </Modal>
      )}
    </div>
  );
}

export default function GatesPage() {
  return (
    <RequireRole role="gate_admin">
      <GatesContent />
    </RequireRole>
  );
}
