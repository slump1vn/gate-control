'use client';

import { FormEvent, useCallback, useState } from 'react';
import {
  createPlateAlert, deletePlateAlert, getPlateAlerts, sendTelegramTest, updatePlateAlert,
} from '@/lib/gate-api';
import type { PlateAlert, PlateAlertInput, TelegramStatus, TelegramTestResult } from '@/lib/gate-api';
import { usePolling } from '@/hooks/usePolling';
import RequireRole from '@/components/RequireRole';
import { useI18n } from '@/components/I18nContext';
import PlateAlertForm from '@/components/PlateAlertForm';
import PlateAlertTable from '@/components/PlateAlertTable';
import Spinner from '@/components/Spinner';
import { Alert, Modal, PageHeader, inputClass, primaryButton, secondaryButton } from '@/components/ui';

function AlertsContent() {
  const { t } = useI18n();
  const [alerts, setAlerts] = useState<PlateAlert[]>([]);
  const [telegram, setTelegram] = useState<TelegramStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editing, setEditing] = useState<PlateAlert | 'new' | null>(null);
  const [version, setVersion] = useState(0);
  const [testChat, setTestChat] = useState('');
  const [testing, setTesting] = useState(false);
  const [testResults, setTestResults] = useState<TelegramTestResult[]>([]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await getPlateAlerts();
      setAlerts(data.results);
      setTelegram(data.telegram);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('alerts.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [t]);

  usePolling(load, 30000, String(version));
  const reload = () => setVersion((v) => v + 1);

  const save = async (data: PlateAlertInput) => {
    if (editing === 'new') {
      const a = await createPlateAlert(data);
      setNotice(t('alerts.added', { plate: a.plate_display }));
    } else if (editing) {
      const a = await updatePlateAlert(editing.id, data);
      setNotice(t('alerts.saved', { plate: a.plate_display }));
    }
    setEditing(null);
    reload();
  };

  const toggle = async (a: PlateAlert) => {
    try {
      await updatePlateAlert(a.id, { is_active: !a.is_active });
      setNotice(t('alerts.saved', { plate: a.plate_display }));
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('alerts.saveFailed'));
    }
  };

  const remove = async (a: PlateAlert) => {
    if (!window.confirm(t('alerts.confirmDelete', { plate: a.plate_display }))) return;
    try {
      await deletePlateAlert(a.id);
      setNotice(t('alerts.deleted', { plate: a.plate_display }));
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('alerts.saveFailed'));
    }
  };

  const test = async (e: FormEvent) => {
    e.preventDefault();
    setTesting(true);
    setTestResults([]);
    setError(null);
    try {
      const data = await sendTelegramTest(testChat.trim() || undefined);
      setTestResults(data.results);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('alerts.saveFailed'));
    } finally {
      setTesting(false);
    }
  };

  const editingAlert = editing === 'new' ? null : editing;
  const minutes = Math.round((telegram?.cooldown_seconds ?? 300) / 60);

  return (
    <div className="max-w-7xl mx-auto px-4 py-8">
      <PageHeader title={t('alerts.title')}>
        <button className={primaryButton} onClick={() => setEditing('new')}>{t('alerts.add')}</button>
      </PageHeader>

      <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">{t('alerts.intro', { minutes })}</p>

      <div className="space-y-3 mb-4">
        {telegram && !telegram.configured && <Alert kind="warning">{t('alerts.notConfigured')}</Alert>}
        {telegram?.configured && telegram.default_recipients === 0 && (
          <Alert kind="info">{t('alerts.noDefaultRecipients')}</Alert>
        )}
        {error && <Alert>{error}</Alert>}
        {notice && <Alert kind="success">{notice}</Alert>}
      </div>

      {telegram?.configured && (
        <form onSubmit={test} className="flex flex-col sm:flex-row sm:items-center gap-2 mb-2">
          <span className="text-sm text-gray-600 dark:text-gray-400 sm:mr-2">
            {t('alerts.defaultRecipients', { n: telegram.default_recipients })}
          </span>
          <input
            className={`${inputClass} sm:w-72 font-mono`}
            placeholder={t('alerts.testChat')}
            aria-label={t('alerts.testChat')}
            value={testChat}
            onChange={(e) => setTestChat(e.target.value)}
          />
          <button type="submit" className={secondaryButton} disabled={testing}>
            {testing ? t('alerts.testing') : t('alerts.test')}
          </button>
        </form>
      )}
      {testResults.length > 0 && (
        <div className="space-y-2 mb-4">
          {testResults.map((r) => (
            <Alert key={r.chat_id} kind={r.ok ? 'success' : 'error'}>
              {r.ok ? t('alerts.testOk', { chat: r.chat_id }) : t('alerts.testFailed', { chat: r.chat_id, error: r.error })}
            </Alert>
          ))}
        </div>
      )}

      <div className="mt-4">
        {loading ? (
          <Spinner />
        ) : alerts.length === 0 ? (
          <div className="text-center py-12 text-gray-500 dark:text-gray-400">{t('alerts.none')}</div>
        ) : (
          <PlateAlertTable alerts={alerts} onEdit={setEditing} onToggle={toggle} onDelete={remove} />
        )}
      </div>

      {editing && (
        <Modal
          title={editing === 'new' ? t('alerts.add') : t('alerts.edit', { plate: editingAlert!.plate_display })}
          onClose={() => setEditing(null)}
        >
          <PlateAlertForm alert={editingAlert} onSubmit={save} onCancel={() => setEditing(null)} />
        </Modal>
      )}
    </div>
  );
}

export default function AlertsPage() {
  return (
    <RequireRole role="gate_admin">
      <AlertsContent />
    </RequireRole>
  );
}
