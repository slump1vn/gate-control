'use client';

import { useCallback, useEffect, useState } from 'react';
import type { ControllerJob, ControllerJobsResponse, RemoteButton, RemoteCodeInfo } from '@/lib/gate-api';
import { ApiError, REMOTE_BUTTONS, getControllerJobs, queueControllerJob } from '@/lib/gate-api';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import { useI18n } from './I18nContext';
import { Alert, Badge, primaryButton, secondaryButton } from './ui';
import type { BadgeColor } from './ui';

interface RemoteCodesPanelProps {
  gateId: number;
  /** A simulated gate hears a made-up remote at once. */
  simulated?: boolean;
  load?: (gateId: number) => Promise<ControllerJobsResponse>;
  queue?: typeof queueControllerJob;
  pollMs?: number;
}

const CAPTURE_SECONDS = 10;
const ACTIVE = new Set<ControllerJob['state']>(['queued', 'dispatched', 'running']);
const STATE_COLOR: Record<ControllerJob['state'], BadgeColor> = {
  queued: 'yellow', dispatched: 'yellow', running: 'blue', done: 'green', failed: 'red', expired: 'gray',
};

function Code({ info }: { info?: Partial<RemoteCodeInfo> | null }) {
  const { t } = useI18n();
  if (!info?.fingerprint) return <span className="text-gray-400">{t('remote.none')}</span>;
  return (
    <span title={t('remote.fingerprintHint')}>
      <code className="font-mono">{info.fingerprint}</code>
      <span className="text-xs text-gray-500 dark:text-gray-400">
        {info.bits ? ` · ${info.bits} bit` : ''}{info.pulse_us ? ` · ${info.pulse_us} µs` : ''}
      </span>
    </span>
  );
}

/**
 * The 433 MHz controller's remote codes, captured from here: the agent tells
 * the controller to listen while the installer holds the remote's button near
 * it, then to keep what it heard. Codes never leave the controller; what is
 * shown are fingerprints, enough to tell whether two codes are the same.
 */
export default function RemoteCodesPanel({
  gateId, simulated = false, load = getControllerJobs, queue = queueControllerJob, pollMs = 2000,
}: RemoteCodesPanelProps) {
  const { t } = useI18n();
  const [data, setData] = useState<ControllerJobsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const refresh = useCallback(async () => {
    try {
      setData(await load(gateId));
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [gateId, load]);

  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const timer = setInterval(() => {
      refresh();
      setNow(Date.now());
    }, pollMs);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, pollMs]);

  const active = data?.jobs.find((j) => ACTIVE.has(j.state));

  const start = async (kind: ControllerJob['kind'], button: RemoteButton) => {
    setBusy(true);
    try {
      await queue(gateId, { kind, button, seconds: CAPTURE_SECONDS });
      setError(null);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError && err.code === 'BUSY' ? t('remote.busy') : err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  if (!data) return error ? <Alert>{error}</Alert> : <p className="text-sm text-gray-500">{t('common.loading')}…</p>;
  if (!data.supported) return <Alert kind="info">{t('remote.notSupported')}</Alert>;

  const capture = data.capture;
  return (
    <div className="space-y-4 text-sm">
      <p className="text-gray-600 dark:text-gray-300">{t(simulated ? 'remote.introSimulated' : 'remote.intro', { seconds: CAPTURE_SECONDS })}</p>
      {error && <Alert>{error}</Alert>}

      <table className="w-full">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">
            <th className="py-1 pr-2">{t('remote.button')}</th>
            <th className="py-1 pr-2">{t('remote.stored')}</th>
            <th className="py-1 pr-2">{t('remote.lastCapture')}</th>
            <th className="py-1" />
          </tr>
        </thead>
        <tbody>
          {REMOTE_BUTTONS.map((button) => {
            const job = data.jobs.find((j) => j.button === button);
            const listening = job?.kind === 'capture' && job.state === 'running';
            const left = listening && job.updated_at
              ? Math.max(0, Math.ceil(job.seconds - (now - Date.parse(job.updated_at)) / 1000)) : 0;
            const keepable = capture?.state === 'captured' && capture.button === button;
            return (
              <tr key={button} className="border-t border-gray-200 dark:border-gray-700 align-top">
                <td className="py-2 pr-2 font-semibold">{t(`remote.${button}` as keyof Dictionary)}</td>
                <td className="py-2 pr-2"><Code info={data.buttons[button]} /></td>
                <td className="py-2 pr-2 space-y-1">
                  {job ? (
                    <>
                      <Badge color={STATE_COLOR[job.state]} title={job.result}>
                        {t(`remote.${job.kind}` as keyof Dictionary)} · {t(`remote.state.${job.state}` as keyof Dictionary)}
                      </Badge>
                      {listening && <div className="text-blue-700 dark:text-blue-300">{t('remote.holdNow', { seconds: left })}</div>}
                      {job.kind === 'capture' && job.state === 'done' && <div><Code info={job.detail} /></div>}
                      {job.state === 'failed' && (
                        <div className="text-xs text-red-600 dark:text-red-400">
                          {job.result === 'nothing' ? t('remote.nothingHeard') : job.result}
                        </div>
                      )}
                    </>
                  ) : <span className="text-gray-400">—</span>}
                </td>
                <td className="py-2 text-right whitespace-nowrap space-x-2">
                  <button className={secondaryButton} disabled={busy || !!active}
                    onClick={() => start('capture', button)}>
                    {t('remote.captureAction')}
                  </button>
                  {keepable && (
                    <button className={primaryButton} disabled={busy || !!active}
                      onClick={() => start('save_code', button)}>
                      {t('remote.saveAction', { button: t(`remote.${button}` as keyof Dictionary) })}
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-xs text-gray-500 dark:text-gray-400">{t('remote.afterSave')}</p>
    </div>
  );
}
