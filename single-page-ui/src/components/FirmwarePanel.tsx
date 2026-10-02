'use client';

import { ChangeEvent, useCallback, useEffect, useState } from 'react';
import type { ControllerFirmware, ControllerJob, ControllerJobsResponse } from '@/lib/gate-api';
import {
  ApiError, deleteControllerFirmware, getControllerFirmware, getControllerJobs, queueFirmwareUpdate,
  uploadControllerFirmware,
} from '@/lib/gate-api';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import { useI18n } from './I18nContext';
import { Alert, Badge, formatDateTime, secondaryButton } from './ui';
import type { BadgeColor } from './ui';

interface FirmwarePanelProps {
  gateId: number;
  load?: (gateId: number) => Promise<ControllerJobsResponse>;
  loadFirmware?: () => Promise<{ results: ControllerFirmware[] }>;
  upload?: (file: File, notes?: string) => Promise<ControllerFirmware>;
  remove?: (id: number) => Promise<unknown>;
  queue?: (gateId: number, firmwareId: number) => Promise<ControllerJob>;
  pollMs?: number;
}

const ACTIVE = new Set<ControllerJob['state']>(['queued', 'dispatched', 'running']);
const STATE_COLOR: Record<ControllerJob['state'], BadgeColor> = {
  queued: 'yellow', dispatched: 'yellow', running: 'blue', done: 'green', failed: 'red', expired: 'gray',
};
const STAGES = ['downloading', 'rebooting', 'verifying', 'done', 'failed', 'rolled_back'];

function kb(size: number) {
  return `${Math.round(size / 1024)} KB`;
}

/**
 * Updating a 433 MHz controller's firmware over WiFi: upload the -app.bin CI
 * builds, then send it to the controller. The controller downloads it,
 * restarts into it, and keeps it only once a heartbeat gets through;
 * otherwise it goes back to the previous firmware on its own.
 */
export default function FirmwarePanel({
  gateId, load = getControllerJobs, loadFirmware = getControllerFirmware, upload = uploadControllerFirmware,
  remove = deleteControllerFirmware, queue = queueFirmwareUpdate, pollMs = 2000,
}: FirmwarePanelProps) {
  const { t } = useI18n();
  const [data, setData] = useState<ControllerJobsResponse | null>(null);
  const [images, setImages] = useState<ControllerFirmware[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setData(await load(gateId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [gateId, load]);

  const refreshImages = useCallback(async () => {
    try {
      setImages((await loadFirmware()).results);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [loadFirmware]);

  useEffect(() => {
    const first = setTimeout(() => {
      refresh();
      refreshImages();
    }, 0);
    const timer = setInterval(refresh, pollMs);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, refreshImages, pollMs]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setNotice(null);
    try {
      await action();
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError && err.code === 'BUSY' ? t('remote.busy') : err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const onFile = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    run(async () => {
      const image = await upload(file);
      setNotice(t('firmware.uploaded', { version: image.version, board: image.board }));
      await refreshImages();
    });
  };

  const send = (image: ControllerFirmware) => {
    if (!window.confirm(t('firmware.confirmUpdate', { version: image.version }))) return;
    run(async () => {
      await queue(gateId, image.id);
      await refresh();
    });
  };

  const drop = (image: ControllerFirmware) => {
    if (!window.confirm(t('firmware.confirmDelete', { version: image.version }))) return;
    run(async () => {
      await remove(image.id);
      await refreshImages();
    });
  };

  if (!data) return error ? <Alert>{error}</Alert> : <p className="text-sm text-gray-500">{t('common.loading')}…</p>;
  if (!data.update_supported) return <Alert kind="info">{t('firmware.notSupported')}</Alert>;

  const job = data.jobs.find((j) => j.kind === 'update');
  const active = data.jobs.find((j) => ACTIVE.has(j.state));
  const stage = job?.detail?.stage;
  const progress = job?.detail?.progress;
  const board = data.board;

  return (
    <div className="space-y-4 text-sm">
      <p className="text-gray-600 dark:text-gray-300">{t('firmware.intro')}</p>
      {error && <Alert>{error}</Alert>}
      {notice && <Alert kind="success">{notice}</Alert>}

      <dl className="grid grid-cols-2 gap-2">
        <dt className="text-gray-500 dark:text-gray-400">{t('firmware.current')}</dt>
        <dd className="font-mono break-all">{data.firmware_version || '—'}</dd>
        <dt className="text-gray-500 dark:text-gray-400">{t('firmware.board')}</dt>
        <dd className="font-mono">{board || t('firmware.boardUnknown')}</dd>
      </dl>

      {job && (
        <div className="border border-gray-200 dark:border-gray-700 rounded-lg p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{t('firmware.lastUpdate')}</span>
            <code className="font-mono text-xs break-all">{job.detail?.version}</code>
            <Badge color={STATE_COLOR[job.state]}>{t(`firmware.state.${job.state}` as keyof Dictionary)}</Badge>
            {job.created_at && <span className="text-xs text-gray-500">{formatDateTime(job.created_at)}</span>}
          </div>
          {stage && STAGES.includes(stage) && (
            <p>{t(`firmware.stage.${stage}` as keyof Dictionary)}{stage === 'downloading' && progress != null ? ` ${progress}%` : ''}</p>
          )}
          {stage === 'downloading' && progress != null && (
            <div className="h-2 rounded bg-gray-200 dark:bg-gray-700 overflow-hidden">
              <div className="h-2 bg-purple-600" style={{ width: `${progress}%` }} />
            </div>
          )}
          {job.state === 'failed' && job.result && (
            <p className="text-red-600 dark:text-red-400">{t('firmware.failedReason', { reason: job.result })}</p>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <label className={`${secondaryButton} cursor-pointer ${busy ? 'opacity-50 pointer-events-none' : ''}`}>
          {t('firmware.upload')}
          <input type="file" accept=".bin,application/octet-stream" className="hidden" onChange={onFile} disabled={busy} />
        </label>
        <span className="text-xs text-gray-500 dark:text-gray-400">{t('firmware.uploadHint')}</span>
      </div>

      {images.length === 0 ? (
        <p className="text-gray-500 dark:text-gray-400">{t('firmware.none')}</p>
      ) : (
        <table className="w-full">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
              <th className="py-1 pr-2">{t('firmware.version')}</th>
              <th className="py-1 pr-2">{t('firmware.board')}</th>
              <th className="py-1 pr-2 hidden sm:table-cell">{t('firmware.uploadedAt')}</th>
              <th className="py-1" />
            </tr>
          </thead>
          <tbody>
            {images.map((image) => {
              const current = image.version === data.firmware_version;
              const wrongBoard = !!board && board !== image.board;
              return (
                <tr key={image.id} className="border-t border-gray-200 dark:border-gray-700 align-top">
                  <td className="py-2 pr-2">
                    <code className="font-mono text-xs break-all">{image.version}</code>
                    <div className="text-xs text-gray-500">{kb(image.size)}{image.notes ? ` · ${image.notes}` : ''}</div>
                  </td>
                  <td className="py-2 pr-2 font-mono text-xs">{image.board}</td>
                  <td className="py-2 pr-2 text-xs hidden sm:table-cell">{image.created_at ? formatDateTime(image.created_at) : ''}</td>
                  <td className="py-2 text-right whitespace-nowrap space-x-3">
                    {current ? (
                      <Badge color="green">{t('firmware.running')}</Badge>
                    ) : (
                      <button onClick={() => send(image)} disabled={busy || !!active || wrongBoard}
                        title={wrongBoard ? t('firmware.wrongBoard', { board: image.board }) : undefined}
                        className="text-purple-600 dark:text-purple-400 hover:underline disabled:opacity-40 disabled:pointer-events-none">
                        {t('firmware.install')}
                      </button>
                    )}
                    <button onClick={() => drop(image)} disabled={busy}
                      className="text-red-600 dark:text-red-400 hover:underline disabled:opacity-40">{t('common.delete')}</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
