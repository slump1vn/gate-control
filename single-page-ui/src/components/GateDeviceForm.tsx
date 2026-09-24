'use client';

import { FormEvent, useState } from 'react';
import { ApiError } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import type { Direction, GateDevice, GateDeviceInput } from '@/lib/gate-api';
import { Alert, Badge, Checkbox, Field, inputClass, primaryButton, secondaryButton } from './ui';

interface GateDeviceFormProps {
  gate?: GateDevice | null;
  cameras: { id: number; name: string }[];
  onSubmit: (data: GateDeviceInput) => Promise<void>;
  onCancel: () => void;
}

export default function GateDeviceForm({ gate, cameras, onSubmit, onCancel }: GateDeviceFormProps) {
  const { t } = useI18n();
  const [data, setData] = useState<GateDeviceInput>(() => ({
    name: gate?.name ?? '',
    location: gate?.location ?? '',
    // A gate watches both ways, so a new one starts with a row for each
    cameras: gate ? gate.cameras.map((c) => ({ camera: c.id, direction: c.direction }))
                  : [{ camera: 0, direction: 'in' }, { camera: 0, direction: 'out' }],
    exit_policy: gate?.exit_policy ?? 'registered',
    controller_type: gate?.controller_type ?? 'simulator',
    controller_url: gate?.controller_url ?? '',
    has_safety_input: gate?.has_safety_input ?? false,
    is_enabled: gate?.is_enabled ?? true,
  }));
  const [token, setToken] = useState('');
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);

  const simulated = data.controller_type === 'simulator';
  const set = <K extends keyof GateDeviceInput>(key: K, value: GateDeviceInput[K]) => setData((d) => ({ ...d, [key]: value }));

  const setRow = (index: number, patch: Partial<GateDeviceInput['cameras'][number]>) =>
    setData((d) => ({ ...d, cameras: d.cameras.map((row, i) => (i === index ? { ...row, ...patch } : row)) }));
  const addRow = () => setData((d) => ({
    ...d,
    cameras: [...d.cameras, { camera: 0, direction: d.cameras.some((r) => r.direction === 'in') ? 'out' : 'in' }],
  }));
  const removeRow = (index: number) =>
    setData((d) => ({ ...d, cameras: d.cameras.filter((_, i) => i !== index) }));

  // Same rule as the server: warn, never block. An installer assigns cameras over time.
  const chosenRows = data.cameras.filter((row) => row.camera > 0);
  const directions = new Set(chosenRows.map((row) => row.direction));
  const incomplete = chosenRows.length < 2 ? t('gateForm.incomplete', { n: chosenRows.length })
    : !directions.has('in') ? t('gateForm.noEntry')
    : !directions.has('out') ? t('gateForm.noExit')
    : '';

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErrors({});
    setFormError('');
    if (!simulated && !/^https?:\/\/[^\s/]+/.test(data.controller_url)) {
      setErrors({ controller_url: [t('gateForm.controllerUrlInvalid')] });
      return;
    }
    const chosen = data.cameras.filter((row) => row.camera > 0);
    if (new Set(chosen.map((row) => row.camera)).size !== chosen.length) {
      setFormError(t('gateForm.duplicateCamera'));
      return;
    }
    setSaving(true);
    try {
      await onSubmit({
        ...data,
        cameras: data.cameras.filter((row) => row.camera > 0),
        ...(token ? { controller_token: token } : {}),
      });
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fieldErrors).length) {
        const { __all__, ...fields } = err.fieldErrors;
        setErrors(fields);
        if (__all__) setFormError(__all__.join(' '));
      } else {
        setFormError(err instanceof Error ? err.message : t('vehicles.saveFailed'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {formError && <Alert>{formError}</Alert>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label={t('gateForm.name')} htmlFor="gate-name" error={errors.name}>
          <input id="gate-name" className={inputClass} required autoFocus value={data.name} onChange={(e) => set('name', e.target.value)} />
        </Field>
        <Field label={t('gateForm.location')} htmlFor="gate-location" error={errors.location}>
          <input id="gate-location" className={inputClass} value={data.location} onChange={(e) => set('location', e.target.value)} />
        </Field>
        <Field label={t('gateForm.exitPolicy')} htmlFor="gate-exit-policy" error={errors.exit_policy}
          hint={t('gateForm.exitPolicyHint')}>
          <select id="gate-exit-policy" className={inputClass} value={data.exit_policy}
            onChange={(e) => set('exit_policy', e.target.value as GateDeviceInput['exit_policy'])}>
            <option value="registered">{t('gateForm.exitRegistered')}</option>
            <option value="any">{t('gateForm.exitAny')}</option>
          </select>
        </Field>
        <Field label={t('gateForm.controller')} htmlFor="gate-controller-type" error={errors.controller_type}
          hint={t(simulated ? 'gateForm.simulatorHint' : 'gateForm.esp32Hint')}>
          <select id="gate-controller-type" className={inputClass} value={data.controller_type}
            onChange={(e) => set('controller_type', e.target.value as 'esp32' | 'simulator')}>
            <option value="simulator">{t('gateForm.simulator')}</option>
            <option value="esp32">{t('gateForm.esp32')}</option>
          </select>
        </Field>
        {!simulated && (
          <Field label={t('gateForm.controllerUrl')} htmlFor="gate-controller-url" error={errors.controller_url} hint={t('gateForm.controllerUrlHint')}>
            <input id="gate-controller-url" className={`${inputClass} font-mono`} placeholder="http://192.168.1.50/"
              value={data.controller_url} onChange={(e) => set('controller_url', e.target.value.trim())} />
          </Field>
        )}
        {!simulated && (
          <Field label={t('gateForm.token')} htmlFor="gate-controller-token" error={errors.controller_token}
            hint={gate?.controller_token_set
              ? <>{t('gateForm.tokenKeep')} <Badge color="green">{t('gateForm.tokenSet')}</Badge></>
              : t('gateForm.tokenNew')}>
            <input id="gate-controller-token" type="password" autoComplete="new-password" className={inputClass}
              value={token} onChange={(e) => setToken(e.target.value)} />
          </Field>
        )}
      </div>
      <fieldset className="border border-gray-200 dark:border-gray-700 rounded-lg p-3">
        <legend className="px-1 text-sm font-medium text-gray-700 dark:text-gray-300">{t('gateForm.cameras')}</legend>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-2">{t('gateForm.camerasHint')}</p>
        {incomplete && <div className="mb-2"><Alert kind="warning">{incomplete}</Alert></div>}
        <div className="space-y-2">
          {data.cameras.map((row, index) => (
            <div key={index} className="flex flex-wrap gap-2 items-center">
              <select
                aria-label={t('gateForm.cameraN', { n: index + 1 })}
                className={`${inputClass} sm:w-64`}
                value={row.camera || ''}
                onChange={(e) => setRow(index, { camera: Number(e.target.value) })}
              >
                <option value="">{t('gateForm.chooseCamera')}</option>
                {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <select
                aria-label={t('gateForm.directionN', { n: index + 1 })}
                className={`${inputClass} sm:w-40`}
                value={row.direction}
                onChange={(e) => setRow(index, { direction: e.target.value as Direction })}
              >
                <option value="in">{t('gate.entry')}</option>
                <option value="out">{t('gate.exit')}</option>
              </select>
              <button type="button" onClick={() => removeRow(index)}
                className="text-sm text-red-600 dark:text-red-400 hover:underline">{t('common.remove')}</button>
            </div>
          ))}
        </div>
        <button type="button" onClick={addRow} className="mt-2 text-sm text-purple-600 dark:text-purple-400 hover:underline">
          {t('gateForm.addCamera')}
        </button>
      </fieldset>

      <div className="flex flex-wrap gap-6">
        <Checkbox id="gate-enabled" label={t('gateForm.enabled')} checked={data.is_enabled} onChange={(v) => set('is_enabled', v)}
          hint={t('gateForm.enabledHint')} />
        <Checkbox id="gate-safety" label={t('gateForm.safety')} checked={data.has_safety_input} onChange={(v) => set('has_safety_input', v)}
          hint={t('gateForm.safetyHint')} />
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onCancel}>{t('common.cancel')}</button>
        <button type="submit" className={primaryButton} disabled={saving}>
          {saving ? t('common.saving') : t(gate ? 'gateForm.submitEdit' : 'gateForm.submitNew')}
        </button>
      </div>
    </form>
  );
}
