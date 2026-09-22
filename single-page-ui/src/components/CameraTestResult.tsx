import type { CameraTestResult as Result } from '@/lib/gate-api';
import { Alert } from './ui';

const STEP_LABELS: Record<string, string> = {
  host: 'Host allowed and resolved',
  rtsp_port: 'RTSP port reachable',
  snapshot: 'Snapshot authenticated and image received',
};

function StepIcon({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="text-gray-400" aria-label="skipped">–</span>;
  return ok
    ? <span className="text-green-600 dark:text-green-400" aria-label="passed">✓</span>
    : <span className="text-red-600 dark:text-red-400" aria-label="failed">✗</span>;
}

export default function CameraTestResult({ result }: { result: Result }) {
  return (
    <div className="space-y-2">
      <Alert kind={result.ok ? 'success' : 'error'}>
        {result.ok ? 'Connection test passed.' : 'Connection test failed.'}
        {!result.recorded && ' (Unsaved settings: the result is not stored on the camera.)'}
      </Alert>
      <ul className="text-sm space-y-1">
        {result.steps.map((s) => (
          <li key={s.name} className="flex gap-2">
            <StepIcon ok={s.ok} />
            <span className="font-medium">{STEP_LABELS[s.name] ?? s.name}</span>
            {s.message && <span className="text-gray-500 dark:text-gray-400">— {s.message}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}
