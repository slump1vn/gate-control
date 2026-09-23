'use client';

import { useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams, useRouter } from 'next/navigation';
import {
  createCamera, deleteCamera, getCamera, getCameraPresets, getConfigChanges, testCamera, updateCamera,
} from '@/lib/gate-api';
import type { Camera, CameraInput, CameraPresets, ConfigChange } from '@/lib/gate-api';
import { usePolling } from '@/hooks/usePolling';
import RequireRole from '@/components/RequireRole';
import CameraForm from '@/components/CameraForm';
import CameraStatusBadge from '@/components/CameraStatusBadge';
import TriggerReadout from '@/components/TriggerReadout';
import ConfigChangeList from '@/components/ConfigChangeList';
import Spinner from '@/components/Spinner';
import { Alert, PageHeader, cardClass, formatDateTime } from '@/components/ui';

function CameraEditContent() {
  const params = useParams();
  const router = useRouter();
  const isNew = params.id === 'new';
  const id = isNew ? null : Number(params.id);

  const [presets, setPresets] = useState<CameraPresets | null>(null);
  const [camera, setCamera] = useState<Camera | null>(null);
  const [status, setStatus] = useState<Pick<Camera, 'agent_status' | 'agent_status_at' | 'agent_trigger' | 'last_test_at' | 'last_test_ok' | 'last_test_error'> | null>(null);
  const [history, setHistory] = useState<ConfigChange[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadHistory = useCallback(async (cameraId: number) => {
    try {
      setHistory((await getConfigChanges({ object_type: 'camera', object_id: cameraId, page_size: 20 })).results);
    } catch { /* history is secondary */ }
  }, []);

  useEffect(() => {
    getCameraPresets().then(setPresets).catch((err) => setError(err.message));
    if (id !== null) {
      getCamera(id).then((c) => { setCamera(c); setStatus(c); }).catch((err) => setError(err.message));
      getConfigChanges({ object_type: 'camera', object_id: id, page_size: 20 })
        .then((r) => setHistory(r.results)).catch(() => {});
    }
  }, [id]);

  // Live agent status without disturbing the form being edited.
  const pollStatus = useCallback(async () => {
    if (id === null) return;
    const c = await getCamera(id);
    setStatus(c);
  }, [id]);
  usePolling(pollStatus, id !== null ? 5000 : null);

  const save = async (data: CameraInput) => {
    if (camera) {
      const saved = await updateCamera(camera.id, data);
      setCamera(saved);
      setStatus(saved);
      setNotice(`Saved. The gate agent picks up the change within 30 seconds (config version ${saved.config_version}).`);
      loadHistory(saved.id);
    } else {
      const saved = await createCamera(data);
      router.replace(`/manage/cameras/${saved.id}`);
    }
  };

  const remove = async () => {
    if (!camera) return;
    const gates = camera.gates.map((g) => g.name).join(', ');
    if (!window.confirm(`Delete camera "${camera.name}"?${gates ? `\n\nGates using it (${gates}) will have no camera.` : ''}`)) return;
    try {
      await deleteCamera(camera.id);
      router.replace('/manage/cameras');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Delete failed');
    }
  };

  if (error && !presets) return <div className="max-w-3xl mx-auto px-4 py-12"><Alert>{error}</Alert></div>;
  if (!presets || (!isNew && !camera)) return error ? <div className="max-w-3xl mx-auto px-4 py-12"><Alert>{error}</Alert></div> : <Spinner className="py-24" />;

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <Link href="/manage/cameras" className="text-sm text-purple-600 dark:text-purple-400 hover:underline">← Cameras</Link>
      <PageHeader title={camera ? camera.name : 'Add camera'}>
        {camera && (
          <button onClick={remove} className="text-sm text-red-600 dark:text-red-400 hover:underline">Delete camera</button>
        )}
      </PageHeader>

      <div className="space-y-3 mb-4">
        {error && <Alert>{error}</Alert>}
        {notice && <Alert kind="success">{notice}</Alert>}
      </div>

      {camera && status && (
        <div className={`${cardClass} p-4 mb-6 grid grid-cols-1 md:grid-cols-3 gap-3 text-sm`}>
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">Agent</div>
            <CameraStatusBadge status={status.agent_status} />
            {status.agent_status_at && <span className="ml-2 text-gray-500 dark:text-gray-400">{formatDateTime(status.agent_status_at)}</span>}
            <div className="mt-2"><TriggerReadout readout={status.agent_trigger} /></div>
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">Last saved test</div>
            {status.last_test_at ? (
              <span className={status.last_test_ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                {status.last_test_ok ? 'Passed' : `Failed${status.last_test_error ? `: ${status.last_test_error}` : ''}`} · {formatDateTime(status.last_test_at)}
              </span>
            ) : <span className="text-gray-400">never</span>}
          </div>
          <div>
            <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">Used by</div>
            {camera.gates.map((g) => g.name).join(', ') || <span className="text-gray-400">no gate</span>}
          </div>
        </div>
      )}

      <CameraForm key={camera?.id ?? 'new'} camera={camera} presets={presets} onSave={save} onTest={testCamera}
        onCancel={() => router.push('/manage/cameras')} />

      {camera && (
        <section className={`${cardClass} p-5 mt-6`}>
          <h2 className="font-semibold mb-3">Change history</h2>
          <ConfigChangeList changes={history} />
        </section>
      )}
    </div>
  );
}

export default function CameraEditClient() {
  return (
    <RequireRole role="gate_admin">
      <CameraEditContent />
    </RequireRole>
  );
}
