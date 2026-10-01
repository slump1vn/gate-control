'use client';

import { FormEvent, useState } from 'react';
import { ApiError } from '@/lib/gate-api';
import type { AlertDirections, PlateAlert, PlateAlertInput } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import { Alert, Checkbox, Field, inputClass, primaryButton, secondaryButton } from './ui';

interface PlateAlertFormProps {
  alert?: PlateAlert | null;
  onSubmit: (data: PlateAlertInput) => Promise<void>;
  onCancel: () => void;
}

const EMPTY: PlateAlertInput = { plate_display: '', label: '', directions: 'both', chat_ids: '', is_active: true };

export default function PlateAlertForm({ alert, onSubmit, onCancel }: PlateAlertFormProps) {
  const { t } = useI18n();
  const [data, setData] = useState<PlateAlertInput>(() => (alert
    ? {
      plate_display: alert.plate_display, label: alert.label, directions: alert.directions,
      chat_ids: alert.chat_ids, is_active: alert.is_active,
    }
    : EMPTY));
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);

  const set = <K extends keyof PlateAlertInput>(key: K, value: PlateAlertInput[K]) => setData((d) => ({ ...d, [key]: value }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErrors({});
    setFormError('');
    setSaving(true);
    try {
      await onSubmit(data);
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fieldErrors).length) {
        const { __all__, ...fields } = err.fieldErrors;
        setErrors(fields);
        if (__all__) setFormError(__all__.join(' '));
      } else {
        setFormError(err instanceof Error ? err.message : t('alerts.saveFailed'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {formError && <Alert>{formError}</Alert>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label={t('alerts.plate')} htmlFor="alert-plate" error={errors.plate_display}>
          <input id="alert-plate" className={`${inputClass} font-mono`} required autoFocus={!alert}
            value={data.plate_display} onChange={(e) => set('plate_display', e.target.value)} />
        </Field>
        <Field label={t('alerts.label')} htmlFor="alert-label" error={errors.label} hint={t('alerts.labelHint')}>
          <input id="alert-label" className={inputClass} value={data.label} onChange={(e) => set('label', e.target.value)} />
        </Field>
        <Field label={t('alerts.directions')} htmlFor="alert-directions" error={errors.directions}>
          <select id="alert-directions" className={inputClass} value={data.directions}
            onChange={(e) => set('directions', e.target.value as AlertDirections)}>
            <option value="both">{t('alerts.dirBoth')}</option>
            <option value="in">{t('alerts.dirIn')}</option>
            <option value="out">{t('alerts.dirOut')}</option>
          </select>
        </Field>
        <div className="flex items-end pb-2">
          <Checkbox id="alert-active" label={t('alerts.active')} checked={data.is_active}
            onChange={(v) => set('is_active', v)} />
        </div>
        <div className="sm:col-span-2">
          <Field label={t('alerts.chatIds')} htmlFor="alert-chats" error={errors.chat_ids} hint={t('alerts.chatIdsHint')}>
            <input id="alert-chats" className={`${inputClass} font-mono`} value={data.chat_ids}
              onChange={(e) => set('chat_ids', e.target.value)} />
          </Field>
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onCancel}>{t('common.cancel')}</button>
        <button type="submit" className={primaryButton} disabled={saving}>
          {saving ? t('common.saving') : t(alert ? 'alerts.submitEdit' : 'alerts.submitNew')}
        </button>
      </div>
    </form>
  );
}
