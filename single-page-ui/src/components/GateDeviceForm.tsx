'use client';

import { FormEvent, useState } from 'react';
import { ApiError } from '@/lib/gate-api';
import type { GateDevice, GateDeviceInput } from '@/lib/gate-api';
import { Alert, Badge, Checkbox, Field, inputClass, primaryButton, secondaryButton } from './ui';

interface GateDeviceFormProps {
  gate?: GateDevice | null;
  cameras: { id: number; name: string }[];
  onSubmit: (data: GateDeviceInput) => Promise<void>;
  onCancel: () => void;
}

export default function GateDeviceForm({ gate, cameras, onSubmit, onCancel }: GateDeviceFormProps) {
  const [data, setData] = useState<GateDeviceInput>(() => ({
    name: gate?.name ?? '',
    location: gate?.location ?? '',
    direction: gate?.direction ?? 'in',
    camera: gate?.camera?.id ?? null,
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

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErrors({});
    setFormError('');
    if (!simulated && !/^https?:\/\/[^\s/]+/.test(data.controller_url)) {
      setErrors({ controller_url: ['Enter the controller address, e.g. http://192.168.1.50/'] });
      return;
    }
    setSaving(true);
    try {
      await onSubmit({ ...data, ...(token ? { controller_token: token } : {}) });
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fieldErrors).length) {
        const { __all__, ...fields } = err.fieldErrors;
        setErrors(fields);
        if (__all__) setFormError(__all__.join(' '));
      } else {
        setFormError(err instanceof Error ? err.message : 'Save failed');
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {formError && <Alert>{formError}</Alert>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Name" htmlFor="gate-name" error={errors.name}>
          <input id="gate-name" className={inputClass} required autoFocus value={data.name} onChange={(e) => set('name', e.target.value)} />
        </Field>
        <Field label="Location" htmlFor="gate-location" error={errors.location}>
          <input id="gate-location" className={inputClass} value={data.location} onChange={(e) => set('location', e.target.value)} />
        </Field>
        <Field label="Direction" htmlFor="gate-direction" error={errors.direction}>
          <select id="gate-direction" className={inputClass} value={data.direction} onChange={(e) => set('direction', e.target.value as 'in' | 'out')}>
            <option value="in">Entry</option>
            <option value="out">Exit</option>
          </select>
        </Field>
        <Field label="Camera" htmlFor="gate-camera" error={errors.camera}>
          <select id="gate-camera" className={inputClass} value={data.camera ?? ''}
            onChange={(e) => set('camera', e.target.value ? Number(e.target.value) : null)}>
            <option value="">No camera</option>
            {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </Field>
        <Field label="Controller" htmlFor="gate-controller-type" error={errors.controller_type}
          hint={simulated ? 'Runs in the LPR service: no hardware moves. Use it to test recognition before the ESP32 is installed.' : 'The ESP32 relay board wired to COM / UP / DOWN / STOP.'}>
          <select id="gate-controller-type" className={inputClass} value={data.controller_type}
            onChange={(e) => set('controller_type', e.target.value as 'esp32' | 'simulator')}>
            <option value="simulator">Simulated barrier</option>
            <option value="esp32">ESP32 relay controller</option>
          </select>
        </Field>
        {!simulated && (
          <Field label="Controller URL" htmlFor="gate-controller-url" error={errors.controller_url} hint="Reached by the gate agent, not by browsers.">
            <input id="gate-controller-url" className={`${inputClass} font-mono`} placeholder="http://192.168.1.50/"
              value={data.controller_url} onChange={(e) => set('controller_url', e.target.value.trim())} />
          </Field>
        )}
        {!simulated && (
          <Field label="Controller token" htmlFor="gate-controller-token" error={errors.controller_token}
            hint={gate?.controller_token_set
              ? <>Leave empty to keep the current token. <Badge color="green">Token set</Badge></>
              : 'Must match the token flashed into the ESP32.'}>
            <input id="gate-controller-token" type="password" autoComplete="new-password" className={inputClass}
              value={token} onChange={(e) => setToken(e.target.value)} />
          </Field>
        )}
      </div>
      <div className="flex flex-wrap gap-6">
        <Checkbox id="gate-enabled" label="Enabled" checked={data.is_enabled} onChange={(v) => set('is_enabled', v)}
          hint="A disabled gate denies every read." />
        <Checkbox id="gate-safety" label="Has a safety sensor" checked={data.has_safety_input} onChange={(v) => set('has_safety_input', v)}
          hint="Photocell or loop wired to the controller. Required before the software may close the arm." />
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onCancel}>Cancel</button>
        <button type="submit" className={primaryButton} disabled={saving}>{saving ? 'Saving…' : gate ? 'Save gate' : 'Add gate'}</button>
      </div>
    </form>
  );
}
