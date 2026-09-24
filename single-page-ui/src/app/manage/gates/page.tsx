'use client';

import { useCallback, useEffect, useState } from 'react';
import { usePolling } from '@/hooks/usePolling';
import Link from 'next/link';
import { getApiBase } from '@/lib/api';
import { createGateDevice, deleteGateDevice, getCameras, getGateDevices, updateGateDevice } from '@/lib/gate-api';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import type { GateDevice, GateDeviceInput } from '@/lib/gate-api';
import RequireRole from '@/components/RequireRole';
import GateDeviceForm from '@/components/GateDeviceForm';
import { useI18n } from '@/components/I18nContext';
import Spinner from '@/components/Spinner';
import { Alert, Badge, Modal, PageHeader, formatDateTime, primaryButton } from '@/components/ui';

function GatesContent() {
  const { t } = useI18n();
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
      setError(err instanceof Error ? err.message : t('gates.loadFailed'));
    }
  }, [t]);

  usePolling(load, 15000);
  useEffect(() => { getApiBase().then(setApiBase); }, []);

  const save = async (data: GateDeviceInput) => {
    const saved = editing === 'new' || !editing ? await createGateDevice(data) : await updateGateDevice(editing.id, data);
    setNotice(t('gates.saved', { name: saved.name }));
    setEditing(null);
    load();
  };

  const remove = async (gate: GateDevice) => {
    if (!window.confirm(t('gates.confirmDelete', { name: gate.name }))) return;
    try {
      await deleteGateDevice(gate.id);
      setNotice(t('gates.deleted', { name: gate.name }));
      load();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('vehicles.saveFailed'));
    }
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title={t('gates.title')}>
        <button className={primaryButton} onClick={() => setEditing('new')}>{t('gates.add')}</button>
      </PageHeader>

      <div className="space-y-3 mb-4">
        {error && <Alert>{error}</Alert>}
        {notice && <Alert kind="success">{notice}</Alert>}
      </div>

      {!gates ? (
        error ? null : <Spinner />
      ) : gates.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">
          {t('gates.none')}
        </div>
      ) : (
        <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-xl">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 dark:bg-[#1a1a1a] text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
              <tr>
                <th className="px-4 py-3">{t('gates.gate')}</th>
                <th className="px-4 py-3">{t('gate.cameras')}</th>
                <th className="px-4 py-3">{t('gates.controller')}</th>
                <th className="px-4 py-3">{t('gates.state')}</th>
                <th className="px-4 py-3 text-right"><span className="sr-only">{t('vehicles.actions')}</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {gates.map((g) => (
                <tr key={g.id}>
                  <td className="px-4 py-3">
                    <div className="font-medium">{g.name}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400">{g.location}</div>
                  </td>
                  <td className="px-4 py-3">
                    {g.cameras.length === 0 ? <span className="text-gray-400">{t('common.none')}</span> : (
                      <ul className="space-y-0.5">
                        {g.cameras.map((c) => (
                          <li key={c.id} className="flex items-center gap-1">
                            <Badge color={c.direction === 'in' ? 'blue' : 'purple'}>
                              {t(`gate.${c.direction === 'in' ? 'entry' : 'exit'}` as keyof Dictionary)}
                            </Badge>
                            <Link href={`/manage/cameras/${c.id}`} className="text-purple-600 dark:text-purple-400 hover:underline">{c.name}</Link>
                          </li>
                        ))}
                      </ul>
                    )}
                    {g.camera_warning && (
                      <div className="text-xs text-yellow-700 dark:text-yellow-400 mt-1" title={g.camera_warning}>
                        ⚠ {g.camera_warning}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    {g.controller_type === 'simulator'
                      ? <Badge color="purple">{t('gate.simulated')}</Badge>
                      : <><div className="font-mono text-xs">{g.controller_url || '—'}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          {g.controller_token_set ? t('gates.tokenSet') : <span className="text-red-600 dark:text-red-400">{t('gates.noToken')}</span>}
                          {g.firmware_version && ` · fw ${g.firmware_version}`}
                        </div></>}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {g.is_enabled ? <Badge color="green">{t('gates.enabled')}</Badge> : <Badge>{t('gate.disabled')}</Badge>}
                      <Badge color={g.online ? 'green' : 'red'} title={g.last_seen ? formatDateTime(g.last_seen) : t('common.never')}>
                        {t(g.online ? 'gates.online' : 'gates.offline')}
                      </Badge>
                      {g.has_safety_input && <Badge color="blue">{t('gates.safety')}</Badge>}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-right whitespace-nowrap space-x-3">
                    <a href={`${apiBase}/admin/lpr_app/gatedevice/${g.id}/test/`} target="_blank" rel="noopener noreferrer"
                      className="text-purple-600 dark:text-purple-400 hover:underline" title={t('gates.testRecognitionHint')}>
                      {t('gates.testRecognition')}
                    </a>
                    <button onClick={() => setEditing(g)} className="text-purple-600 dark:text-purple-400 hover:underline">{t('common.edit')}</button>
                    <button onClick={() => remove(g)} className="text-red-600 dark:text-red-400 hover:underline">{t('common.delete')}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editing && (
        <Modal
          title={editing === 'new' ? t('gates.add') : t('gates.edit', { name: editing.name })}
          onClose={() => setEditing(null)}
        >
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
