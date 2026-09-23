'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { getApiBase } from '@/lib/api';
import { getAccessEvents, getGateStatus, REASONS, sendOverride } from '@/lib/gate-api';
import type { AccessEvent, AccessEventFilters } from '@/lib/gate-api';
import { usePolling } from '@/hooks/usePolling';
import RequireRole from '@/components/RequireRole';
import EventTable from '@/components/EventTable';
import FullscreenPreview from '@/components/FullscreenPreview';
import Pagination from '@/components/Pagination';
import Spinner from '@/components/Spinner';
import { Alert, Checkbox, PageHeader, inputClass, secondaryButton } from '@/components/ui';

const PAGE_SIZE = 20;
const REFRESH_MS = 5000;

type Filters = Omit<AccessEventFilters, 'page' | 'page_size'>;

const EMPTY_FILTERS: Filters = { gate: '', direction: '', decision: '', reason: '', plate: '', date_from: '', date_to: '', is_test: 'false' };

function EventsContent() {
  const [apiBase, setApiBase] = useState('');
  const [gates, setGates] = useState<{ id: number; name: string }[]>([]);
  const [draft, setDraft] = useState<Filters>(EMPTY_FILTERS);
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [page, setPage] = useState(1);
  const [events, setEvents] = useState<AccessEvent[]>([]);
  const [count, setCount] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: 'success' | 'error'; text: string } | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [preview, setPreview] = useState<{ src: string; alt: string } | null>(null);

  useEffect(() => {
    getApiBase().then(setApiBase);
    getGateStatus().then((s) => setGates(s.gates.map((g) => ({ id: g.id, name: g.name })))).catch(() => {});
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await getAccessEvents({ ...filters, page, page_size: PAGE_SIZE });
      setEvents(data.results);
      setCount(data.count);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load events');
    } finally {
      setLoaded(true);
    }
  }, [filters, page]);

  // New events arrive on the first page; later pages are left alone while browsing.
  usePolling(load, autoRefresh && page === 1 ? REFRESH_MS : null, `${JSON.stringify(filters)}|${page}`);

  const apply = (e: FormEvent) => {
    e.preventDefault();
    setFilters(draft);
    setPage(1);
  };

  const reset = () => {
    setDraft(EMPTY_FILTERS);
    setFilters(EMPTY_FILTERS);
    setPage(1);
  };

  const openAnyway = async (event: AccessEvent) => {
    if (!event.gate) return;
    const who = event.near_miss_vehicle
      ? `\n\nThe read ${event.plate_normalized || '(none)'} is close to ${event.near_miss_vehicle.plate_display} (${event.near_miss_vehicle.owner_name}).`
      : '';
    if (!window.confirm(`Open ${event.gate.name} now?${who}\n\nThis is recorded under your name.`)) return;
    try {
      await sendOverride(event.gate.id, 'open');
      setNotice({ kind: 'success', text: `OPEN sent to ${event.gate.name}.` });
      load();
    } catch (err) {
      setNotice({ kind: 'error', text: `Open failed: ${err instanceof Error ? err.message : err}` });
    }
  };

  const set = (key: keyof Filters, value: string) => setDraft((d) => ({ ...d, [key]: value }));

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title="Access events">
        <Checkbox id="events-auto-refresh" label="Auto-refresh" checked={autoRefresh} onChange={setAutoRefresh} />
      </PageHeader>

      <form onSubmit={apply} className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-9 gap-2 mb-4 items-end">
        <select aria-label="Gate" className={inputClass} value={draft.gate} onChange={(e) => set('gate', e.target.value)}>
          <option value="">All gates</option>
          {gates.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
        <select aria-label="Direction" className={inputClass} value={draft.direction} onChange={(e) => set('direction', e.target.value)}>
          <option value="">In and out</option>
          <option value="in">Entering</option>
          <option value="out">Leaving</option>
        </select>
        <select aria-label="Decision" className={inputClass} value={draft.decision} onChange={(e) => set('decision', e.target.value)}>
          <option value="">All decisions</option>
          <option value="granted">Granted</option>
          <option value="denied">Denied</option>
          <option value="manual">Manual</option>
        </select>
        <select aria-label="Reason" className={inputClass} value={draft.reason} onChange={(e) => set('reason', e.target.value)}>
          <option value="">All reasons</option>
          {Object.entries(REASONS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <input aria-label="Plate" placeholder="Plate" className={`${inputClass} font-mono`} value={draft.plate} onChange={(e) => set('plate', e.target.value)} />
        <input aria-label="From date" type="date" className={inputClass} value={draft.date_from} onChange={(e) => set('date_from', e.target.value)} />
        <input aria-label="To date" type="date" className={inputClass} value={draft.date_to} onChange={(e) => set('date_to', e.target.value)} />
        <select aria-label="Test events" className={inputClass} value={draft.is_test} onChange={(e) => set('is_test', e.target.value)}>
          <option value="false">Real events</option>
          <option value="true">Test events</option>
          <option value="">Real and test</option>
        </select>
        <div className="flex gap-2">
          <button type="submit" className={secondaryButton}>Filter</button>
          <button type="button" className={secondaryButton} onClick={reset}>Reset</button>
        </div>
      </form>

      <div className="space-y-3 mb-4">
        {error && <Alert>{error}</Alert>}
        {notice && <Alert kind={notice.kind}>{notice.text}</Alert>}
      </div>

      {!loaded ? (
        <Spinner />
      ) : events.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">No events match these filters</div>
      ) : (
        <>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">{count} event{count !== 1 ? 's' : ''}</p>
          <EventTable events={events} apiBase={apiBase} onOpenAnyway={openAnyway}
            onPreview={(src, alt) => setPreview({ src, alt })} />
          <Pagination currentPage={page} totalPages={Math.ceil(count / PAGE_SIZE)} onPageChange={setPage} />
        </>
      )}

      {preview && <FullscreenPreview src={preview.src} alt={preview.alt} onClose={() => setPreview(null)} />}
    </div>
  );
}

export default function EventsPage() {
  return (
    <RequireRole role="gate_operator">
      <EventsContent />
    </RequireRole>
  );
}
