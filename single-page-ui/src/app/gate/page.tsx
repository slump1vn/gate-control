'use client';

import { useCallback, useState } from 'react';
import { getGateStatus, sendOverride } from '@/lib/gate-api';
import type { Command, GateStatusResponse } from '@/lib/gate-api';
import { usePolling } from '@/hooks/usePolling';
import RequireRole from '@/components/RequireRole';
import GateStatusCard from '@/components/GateStatusCard';
import Spinner from '@/components/Spinner';
import { Alert, Badge, PageHeader, dangerButton } from '@/components/ui';
import { useAuth } from '@/components/AuthContext';
import Link from 'next/link';

const POLL_MS = 1000;

function GateStatusContent() {
  const { hasRole } = useAuth();
  const [status, setStatus] = useState<GateStatusResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const [stoppingAll, setStoppingAll] = useState(false);

  const load = useCallback(async () => {
    try {
      setStatus(await getGateStatus());
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load gate status');
    }
  }, []);

  usePolling(load, POLL_MS);

  const command = useCallback(async (gateId: number, cmd: Command) => {
    const name = status?.gates.find((g) => g.id === gateId)?.name ?? `#${gateId}`;
    try {
      await sendOverride(gateId, cmd);
      setMessage({ kind: 'success', text: `${cmd.toUpperCase()} sent to ${name}.` });
    } catch (err) {
      setMessage({ kind: 'error', text: `${cmd.toUpperCase()} on ${name} failed: ${err instanceof Error ? err.message : err}` });
    }
    load();
  }, [status, load]);

  const stopAll = async () => {
    if (!status) return;
    setStoppingAll(true);
    const results = await Promise.allSettled(status.gates.map((g) => sendOverride(g.id, 'stop')));
    const failed = results.filter((r) => r.status === 'rejected').length;
    setMessage(failed
      ? { kind: 'error', text: `STOP failed on ${failed} of ${results.length} gates. Use the controller's own STOP.` }
      : { kind: 'success', text: `STOP sent to all ${results.length} gates.` });
    setStoppingAll(false);
    load();
  };

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title="Gate status">
        {status && (
          <Badge color={status.mode === 'live' ? 'green' : 'yellow'} title={status.mode === 'live'
            ? 'Granted decisions open the barrier.'
            : 'Decisions are recorded; only simulated barriers move.'}>
            Mode: {status.mode}
          </Badge>
        )}
        <button className={`${dangerButton} px-6 py-3 text-base`} onClick={stopAll} disabled={!status?.gates.length || stoppingAll}>
          {stoppingAll ? 'Stopping…' : 'EMERGENCY STOP'}
        </button>
      </PageHeader>

      <div className="space-y-3 mb-4">
        {error && <Alert>{error}</Alert>}
        {message && <Alert kind={message.kind}>{message.text}</Alert>}
        <p className="text-xs text-gray-500 dark:text-gray-400">
          STOP halts the arm through the gate agent. The agent picks up commands within about a second and drops
          any that are not picked up within 15 seconds. If the agent is down, use the STOP button on the barrier controller.
        </p>
      </div>

      {!status ? (
        error ? null : <Spinner />
      ) : status.gates.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">
          No gates configured.{' '}
          {hasRole('gate_admin') && <Link href="/admin/gates" className="text-purple-600 dark:text-purple-400 hover:underline">Add a gate</Link>}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {status.gates.map((gate) => (
            <GateStatusCard key={gate.id} gate={gate} mode={status.mode} onCommand={command} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function GateStatusPage() {
  return (
    <RequireRole role="gate_operator">
      <GateStatusContent />
    </RequireRole>
  );
}
