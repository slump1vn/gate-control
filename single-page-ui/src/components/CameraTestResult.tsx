'use client';

import type { CameraTestResult as Result } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import { Alert } from './ui';

const STEP_LABELS: Record<string, keyof Dictionary> = {
  host: 'cameraForm.stepHost',
  rtsp_port: 'cameraForm.stepRtsp',
  snapshot: 'cameraForm.stepSnapshot',
};

function StepIcon({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="text-gray-400" aria-label="skipped">–</span>;
  return ok
    ? <span className="text-green-600 dark:text-green-400" aria-label="passed">✓</span>
    : <span className="text-red-600 dark:text-red-400" aria-label="failed">✗</span>;
}

export default function CameraTestResult({ result }: { result: Result }) {
  const { t } = useI18n();
  return (
    <div className="space-y-2">
      <Alert kind={result.ok ? 'success' : 'error'}>
        {t(result.ok ? 'cameraForm.testPassed' : 'cameraForm.testFailed')}
        {!result.recorded && ` ${t('cameraForm.testUnsaved')}`}
      </Alert>
      <ul className="text-sm space-y-1">
        {result.steps.map((s) => (
          <li key={s.name} className="flex gap-2">
            <StepIcon ok={s.ok} />
            <span className="font-medium">{STEP_LABELS[s.name] ? t(STEP_LABELS[s.name]) : s.name}</span>
            {s.message && <span className="text-gray-500 dark:text-gray-400">— {s.message}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
