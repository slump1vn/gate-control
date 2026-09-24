'use client';

import { FormEvent, useCallback, useEffect, useState } from 'react';
import { getApiBase } from '@/lib/api';
import { getAccessEvents, getGateStatus, REASONS, sendOverride } from '@/lib/gate-api';
import { useI18n } from '@/components/I18nContext';
import type { Dictionary } from '@/lib/i18n/dictionaries';
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
  const { t } = useI18n();
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
      setError(err instanceof Error ? err.message : t('events.loadFailed'));
    } finally {
      setLoaded(true);
    }
  }, [filters, page, t]);

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
    const near = event.near_miss_vehicle
      ? t('events.nearMissNote', {
          plate: event.plate_normalized || '—',
          other: event.near_miss_vehicle.plate_display,
          owner: event.near_miss_vehicle.owner_name,
        })
      : '';
    if (!window.confirm(t('events.confirmOpen', { gate: event.gate.name, near }))) return;
    try {
      await sendOverride(event.gate.id, 'open');
      setNotice({ kind: 'success', text: t('events.openSent', { gate: event.gate.name }) });
      load();
    } catch (err) {
      setNotice({ kind: 'error', text: t('events.openFailed', { error: err instanceof Error ? err.message : String(err) }) });
    }
  };

  const set = (key: keyof Filters, value: string) => setDraft((d) => ({ ...d, [key]: value }));

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title={t('events.title')}>
        <Checkbox id="events-auto-refresh" label={t('events.autoRefresh')} checked={autoRefresh} onChange={setAutoRefresh} />
      </PageHeader>

      <form onSubmit={apply} className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-9 gap-2 mb-4 items-end">
        <select aria-label={t('gates.gate')} className={inputClass} value={draft.gate} onChange={(e) => set('gate', e.target.value)}>
          <option value="">{t('events.allGates')}</option>
          {gates.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
        </select>
        <select aria-label={t('events.direction')} className={inputClass} value={draft.direction} onChange={(e) => set('direction', e.target.value)}>
          <option value="">{t('events.bothDirections')}</option>
          <option value="in">{t('events.entering')}</option>
          <option value="out">{t('events.leaving')}</option>
        </select>
        <select aria-label={t('events.decision')} className={inputClass} value={draft.decision} onChange={(e) => set('decision', e.target.value)}>
          <option value="">{t('events.allDecisions')}</option>
          <option value="granted">{t('decision.granted')}</option>
          <option value="denied">{t('decision.denied')}</option>
          <option value="manual">{t('decision.manual')}</option>
        </select>
        <select aria-label={t('events.allReasons')} className={inputClass} value={draft.reason} onChange={(e) => set('reason', e.target.value)}>
          <option value="">{t('events.allReasons')}</option>
          {Object.keys(REASONS).map((value) => (
            <option key={value} value={value}>{t(`reason.${value}` as keyof Dictionary)}</option>
          ))}
        </select>
        <input aria-label={t('vehicles.plate')} placeholder={t('events.platePlaceholder')} className={`${inputClass} font-mono`} value={draft.plate} onChange={(e) => set('plate', e.target.value)} />
        <input aria-label={t('events.fromDate')} type="date" className={inputClass} value={draft.date_from} onChange={(e) => set('date_from', e.target.value)} />
        <input aria-label={t('events.toDate')} type="date" className={inputClass} value={draft.date_to} onChange={(e) => set('date_to', e.target.value)} />
        <select aria-label={t('events.testEvents')} className={inputClass} value={draft.is_test} onChange={(e) => set('is_test', e.target.value)}>
          <option value="false">{t('events.realEvents')}</option>
          <option value="true">{t('events.testEvents')}</option>
          <option value="">{t('events.realAndTest')}</option>
        </select>
        <div className="flex gap-2">
          <button type="submit" className={secondaryButton}>{t('common.filter')}</button>
          <button type="button" className={secondaryButton} onClick={reset}>{t('common.reset')}</button>
        </div>
      </form>

      <div className="space-y-3 mb-4">
        {error && <Alert>{error}</Alert>}
        {notice && <Alert kind={notice.kind}>{notice.text}</Alert>}
      </div>

      {!loaded ? (
        <Spinner />
      ) : events.length === 0 ? (
        <div className="text-center py-12 text-gray-500 dark:text-gray-400">{t('events.none')}</div>
      ) : (
        <>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">{t('events.count', { n: count })}</p>
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
