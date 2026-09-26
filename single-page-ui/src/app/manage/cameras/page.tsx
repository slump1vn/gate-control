'use client';

import { useCallback, useState } from 'react';
import Link from 'next/link';
import { getCameras } from '@/lib/gate-api';
import type { Camera } from '@/lib/gate-api';
import { usePolling } from '@/hooks/usePolling';
import RequireRole from '@/components/RequireRole';
import CameraStatusBadge from '@/components/CameraStatusBadge';
import { useI18n } from '@/components/I18nContext';
import Spinner from '@/components/Spinner';
import { Alert, Badge, PageHeader, formatDateTime, primaryButton } from '@/components/ui';

function CamerasContent() {
  const { t } = useI18n();
  const [cameras, setCameras] = useState<Camera[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCameras(await getCameras());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cameras.loadFailed'));
    }
  }, [t]);

  // Agent status changes on its own; keep the list current.
  usePolling(load, 10000);

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title={t('cameras.title')}>
        <Link href="/manage/cameras/new" className={primaryButton}>{t('cameras.add')}</Link>
      </PageHeader>
      {error && <div className="mb-4"><Alert>{error}</Alert></div>}

      {!cameras ? (
        error ? null : <Spinner />
      ) : cameras.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">
          {t('cameras.none')} <Link href="/manage/cameras/new" className="text-purple-600 dark:text-purple-400 hover:underline">{t('cameras.addFirst')}</Link>
        </div>
      ) : (
        <div className="overflow-x-auto border border-gray-200 dark:border-gray-700 rounded-xl">
          <table className="min-w-full text-sm">
            <thead className="bg-gray-50 dark:bg-[#1a1a1a] text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
              <tr>
                <th className="px-4 py-3">{t('cameras.camera')}</th>
                <th className="px-4 py-3">{t('cameras.address')}</th>
                <th className="px-4 py-3">{t('cameras.gates')}</th>
                <th className="px-4 py-3">{t('cameras.agent')}</th>
                <th className="px-4 py-3 hidden md:table-cell">{t('cameras.lastTest')}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
              {cameras.map((c) => (
                <tr key={c.id}>
                  <td className="px-4 py-3">
                    <Link href={`/manage/cameras/${c.id}`} className="font-medium text-purple-600 dark:text-purple-400 hover:underline">{c.name}</Link>
                    {!c.is_enabled && <> <Badge>{t('gate.disabled')}</Badge></>}
                    <div className="text-xs text-gray-500 dark:text-gray-400 capitalize">{c.vendor}</div>
                  </td>
                  <td className="px-4 py-3 font-mono text-xs">{c.host}<span className="text-gray-400"> · rtsp {c.rtsp_port} · http {c.http_port}</span></td>
                  <td className="px-4 py-3">{c.gates.map((g) => `${g.name} (${t(g.direction === 'in' ? 'gate.entry' : 'gate.exit')})`).join(', ')
                    || <span className="text-gray-400">{t('cameras.unassigned')}</span>}</td>
                  <td className="px-4 py-3"><CameraStatusBadge status={c.agent_status} /></td>
                  <td className="px-4 py-3 hidden md:table-cell">
                    {c.last_test_at ? (
                      <span className={c.last_test_ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'} title={c.last_test_error}>
                        {t(c.last_test_ok ? 'cameras.passed' : 'cameras.failed')} · {formatDateTime(c.last_test_at)}
                      </span>
                    ) : <span className="text-gray-400">{t('common.never')}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function CamerasPage() {
  return (
    <RequireRole role="gate_admin">
      <CamerasContent />
    </RequireRole>
  );
}
