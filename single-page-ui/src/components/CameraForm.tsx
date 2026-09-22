'use client';

import { FormEvent, useMemo, useState } from 'react';
import { ApiError } from '@/lib/gate-api';
import type { Camera, CameraInput, CameraPresets, CameraTestResult as TestResult, Vendor } from '@/lib/gate-api';
import CameraTestResult from './CameraTestResult';
import RoiPicker from './RoiPicker';
import { Alert, Badge, Checkbox, Field, cardClass, inputClass, primaryButton, secondaryButton } from './ui';

export type CameraTestState =
  | { state: 'idle' }
  | { state: 'testing' }
  | { state: 'done'; result: TestResult }
  | { state: 'error'; message: string };

interface CameraFormProps {
  camera?: Camera | null;
  presets: CameraPresets;
  onSave: (data: CameraInput) => Promise<void>;
  onTest: (data: Partial<CameraInput> & { camera_id?: number }) => Promise<TestResult>;
  onCancel?: () => void;
  /** Starting state of the connection test panel (Storybook). */
  initialTest?: CameraTestState;
  /** Start with the read-zone editor open (Storybook). */
  initialRoiEditing?: boolean;
}

type Values = Omit<CameraInput, 'password'>;
type NumberField = 'rtsp_port' | 'http_port' | 'motion_threshold' | 'settle_ms' | 'cooldown_s';

const DEFAULTS: Values = {
  name: '',
  is_enabled: true,
  host: '',
  rtsp_port: 554,
  http_port: 80,
  username: '',
  vendor: 'hikvision',
  main_stream_path: '',
  sub_stream_path: '',
  snapshot_path: '',
  prefer_snapshot: true,
  roi: null,
  motion_threshold: 0.02,
  settle_ms: 800,
  cooldown_s: 5,
};

const PATH_FIELDS = ['main_stream_path', 'sub_stream_path', 'snapshot_path'] as const;

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const HOSTNAME = /^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$/;

/** Same rules as the server's validate_host_syntax; the network allowlist is checked server-side. */
export function hostError(host: string): string | null {
  const h = host.trim();
  if (!h) return 'Host is required.';
  if (/[:/@?\s\\]/.test(h)) return 'Enter only an IP address or hostname, without scheme, port, path or credentials.';
  if (/^[\d.]+$/.test(h)) return IPV4.test(h) ? null : `"${h}" is not a valid IPv4 address.`;
  return HOSTNAME.test(h) ? null : `"${h}" is not a valid IPv4 address or hostname.`;
}

function toValues(camera: Camera | null | undefined, presets: CameraPresets): Values {
  if (!camera) return { ...DEFAULTS, ...presets[DEFAULTS.vendor] };
  const v = {} as Record<string, unknown>;
  (Object.keys(DEFAULTS) as (keyof Values)[]).forEach((k) => { v[k] = camera[k]; });
  return v as unknown as Values;
}

function clientErrors(v: Values, raw: Record<NumberField, string>): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!v.name.trim()) errors.name = 'Name is required.';
  const h = hostError(v.host);
  if (h) errors.host = h;
  for (const port of ['rtsp_port', 'http_port'] as const) {
    const n = Number(raw[port]);
    if (!/^\d+$/.test(raw[port]) || n < 1 || n > 65535) errors[port] = 'Port must be a whole number from 1 to 65535.';
  }
  const t = Number(raw.motion_threshold);
  if (raw.motion_threshold === '' || isNaN(t) || t < 0 || t > 1) errors.motion_threshold = 'Between 0 and 1.';
  for (const f of ['settle_ms', 'cooldown_s'] as const) {
    if (!/^\d+$/.test(raw[f])) errors[f] = 'A whole number, 0 or more.';
  }
  return errors;
}

export default function CameraForm({ camera, presets, onSave, onTest, onCancel, initialTest = { state: 'idle' }, initialRoiEditing = false }: CameraFormProps) {
  const initial = useMemo(() => toValues(camera, presets), [camera, presets]);
  const [values, setValues] = useState<Values>(initial);
  // Number inputs are kept as typed, so a half-typed value is not coerced.
  const [raw, setRaw] = useState<Record<NumberField, string>>(() => ({
    rtsp_port: String(initial.rtsp_port),
    http_port: String(initial.http_port),
    motion_threshold: String(initial.motion_threshold),
    settle_ms: String(initial.settle_ms),
    cooldown_s: String(initial.cooldown_s),
  }));
  const [password, setPassword] = useState('');
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [serverErrors, setServerErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);
  const [test, setTest] = useState<CameraTestState>(initialTest);
  const [roiEditing, setRoiEditing] = useState(initialRoiEditing);
  const [advanced, setAdvanced] = useState(false);

  const current: Values = {
    ...values,
    rtsp_port: Number(raw.rtsp_port),
    http_port: Number(raw.http_port),
    motion_threshold: Number(raw.motion_threshold),
    settle_ms: Number(raw.settle_ms),
    cooldown_s: Number(raw.cooldown_s),
  };
  const errors = clientErrors(values, raw);
  const dirty = JSON.stringify(current) !== JSON.stringify(initial) || password !== '';
  const snapshot = test.state === 'done' ? test.result.image : null;

  // Editing a field clears the server's complaint about it and the summary above the form.
  const clearError = (key: string) => {
    setServerErrors((e) => ({ ...e, [key]: [] }));
    setFormError('');
  };
  const set = <K extends keyof Values>(key: K, value: Values[K]) => {
    setValues((v) => ({ ...v, [key]: value }));
    clearError(key);
  };
  const setNumber = (key: NumberField, value: string) => {
    setRaw((r) => ({ ...r, [key]: value }));
    clearError(key);
  };
  const touch = (key: string) => setTouched((t) => ({ ...t, [key]: true }));

  // A vendor change fills paths that are empty or still hold the previous vendor's preset.
  const changeVendor = (vendor: Vendor) => {
    setValues((v) => {
      const next = { ...v, vendor };
      for (const f of PATH_FIELDS) {
        if (!v[f] || v[f] === presets[v.vendor]?.[f]) next[f] = presets[vendor]?.[f] ?? '';
      }
      return next;
    });
  };

  const errorFor = (key: string): string[] => {
    const server = serverErrors[key] ?? [];
    if (server.length) return server;
    return (touched[key] || touched.__submit) && errors[key] ? [errors[key]] : [];
  };

  const handleApiError = (err: unknown) => {
    if (err instanceof ApiError && Object.keys(err.fieldErrors).length) {
      const { __all__, roi_x, roi_y, roi_w, roi_h, ...fields } = err.fieldErrors;
      const roi = [roi_x, roi_y, roi_w, roi_h].flat().filter(Boolean) as string[];
      setServerErrors({ ...fields, roi });
      setFormError([...(__all__ ?? []), ...roi].join(' ') || 'Please correct the highlighted fields.');
    } else {
      setFormError(err instanceof Error ? err.message : String(err));
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setTouched((t) => ({ ...t, __submit: true }));
    setFormError('');
    if (Object.keys(errors).length) return;
    setSaving(true);
    try {
      await onSave({ ...current, ...(password ? { password } : {}) });
      setPassword('');
    } catch (err) {
      handleApiError(err);
    } finally {
      setSaving(false);
    }
  };

  const runTest = async () => {
    setTouched((t) => ({ ...t, host: true, rtsp_port: true, http_port: true }));
    if (errors.host || errors.rtsp_port || errors.http_port) return;
    setTest({ state: 'testing' });
    try {
      // A saved, unchanged camera is tested as stored and the result recorded on it.
      const payload = camera && !dirty
        ? { camera_id: camera.id }
        : { ...current, ...(camera ? { camera_id: camera.id } : {}), ...(password ? { password } : {}) };
      setTest({ state: 'done', result: await onTest(payload) });
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fieldErrors).length) handleApiError(err);
      setTest({ state: 'error', message: err instanceof Error ? err.message : String(err) });
    }
  };

  const numberInput = (key: NumberField, props: { step?: string; min?: string; max?: string } = {}) => (
    <input id={`camera-${key}`} type="number" inputMode="decimal" className={inputClass} {...props}
      value={raw[key]} onChange={(e) => setNumber(key, e.target.value)} onBlur={() => touch(key)}
      aria-invalid={errorFor(key).length > 0} />
  );

  return (
    <form onSubmit={submit} noValidate className="grid grid-cols-1 xl:grid-cols-2 gap-6">
      <div className={`${cardClass} p-5 space-y-4`}>
        {formError && <Alert>{formError}</Alert>}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="Name" htmlFor="camera-name" error={errorFor('name')}>
            <input id="camera-name" className={inputClass} value={values.name}
              onChange={(e) => set('name', e.target.value)} onBlur={() => touch('name')} />
          </Field>
          <Field label="Vendor" htmlFor="camera-vendor" error={errorFor('vendor')} hint="Fills in the stream and snapshot paths">
            <select id="camera-vendor" className={inputClass} value={values.vendor} onChange={(e) => changeVendor(e.target.value as Vendor)}>
              <option value="hikvision">Hikvision</option>
              <option value="dahua">Dahua</option>
              <option value="generic">Generic / other</option>
            </select>
          </Field>
          <Field label="Host / IP address" htmlFor="camera-host" error={errorFor('host')}
            hint="Local address of the camera, e.g. 192.168.1.64. Must be inside the allowed camera networks.">
            <input id="camera-host" className={`${inputClass} font-mono`} value={values.host} placeholder="192.168.1.64"
              onChange={(e) => set('host', e.target.value.trim())} onBlur={() => touch('host')} aria-invalid={errorFor('host').length > 0} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="RTSP port" htmlFor="camera-rtsp_port" error={errorFor('rtsp_port')}>
              {numberInput('rtsp_port', { min: '1', max: '65535' })}
            </Field>
            <Field label="HTTP port" htmlFor="camera-http_port" error={errorFor('http_port')}>
              {numberInput('http_port', { min: '1', max: '65535' })}
            </Field>
          </div>
          <Field label="Username" htmlFor="camera-username" error={errorFor('username')}>
            <input id="camera-username" className={inputClass} autoComplete="off" value={values.username}
              onChange={(e) => set('username', e.target.value)} />
          </Field>
          <Field
            label="Password"
            htmlFor="camera-password"
            error={serverErrors.password}
            hint={camera?.password_set ? <>Leave empty to keep the current password. <Badge color="green">Password set</Badge></> : 'Stored encrypted; never shown again.'}
          >
            <input id="camera-password" type="password" className={inputClass} autoComplete="new-password"
              placeholder={camera?.password_set ? '••••••••' : ''} value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        </div>

        <div className="space-y-3">
          {PATH_FIELDS.map((f) => (
            <Field key={f} label={{ main_stream_path: 'Main stream path', sub_stream_path: 'Sub stream path', snapshot_path: 'Snapshot path' }[f]}
              htmlFor={`camera-${f}`} error={errorFor(f)}>
              <input id={`camera-${f}`} className={`${inputClass} font-mono text-xs`} value={values[f]}
                placeholder={presets[values.vendor]?.[f] || ''} onChange={(e) => set(f, e.target.value)} />
            </Field>
          ))}
        </div>

        <div className="flex flex-wrap gap-6">
          <Checkbox id="camera-prefer-snapshot" label="Prefer snapshots over RTSP" checked={values.prefer_snapshot}
            onChange={(v) => set('prefer_snapshot', v)} hint="Sharper frames and no video decoding; RTSP is used as a fallback." />
          <Checkbox id="camera-enabled" label="Enabled" checked={values.is_enabled} onChange={(v) => set('is_enabled', v)} />
        </div>

        <div>
          <button type="button" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}
            className="text-sm text-purple-600 dark:text-purple-400 hover:underline">
            {advanced ? '▾' : '▸'} Advanced: trigger tuning
          </button>
          {advanced && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-3">
              <Field label="Motion threshold" htmlFor="camera-motion_threshold" error={errorFor('motion_threshold')}
                hint="Fraction of the zone that must change (0–1)">
                {numberInput('motion_threshold', { step: '0.005', min: '0', max: '1' })}
              </Field>
              <Field label="Settle time (ms)" htmlFor="camera-settle_ms" error={errorFor('settle_ms')}
                hint="Still for this long before reading">
                {numberInput('settle_ms', { step: '100', min: '0' })}
              </Field>
              <Field label="Cooldown (s)" htmlFor="camera-cooldown_s" error={errorFor('cooldown_s')}
                hint="Before re-reading a denied vehicle">
                {numberInput('cooldown_s', { min: '0' })}
              </Field>
            </div>
          )}
        </div>

        <div className="flex flex-wrap justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-700">
          {onCancel && <button type="button" className={secondaryButton} onClick={onCancel}>Cancel</button>}
          <button type="button" className={secondaryButton} onClick={runTest} disabled={test.state === 'testing'}>
            {test.state === 'testing' ? 'Testing…' : 'Test connection'}
          </button>
          <button type="submit" className={primaryButton} disabled={saving}>
            {saving ? 'Saving…' : camera ? 'Save camera' : 'Add camera'}
          </button>
        </div>
      </div>

      <div className={`${cardClass} p-5 space-y-4`}>
        <div className="flex items-center justify-between gap-2">
          <h2 className="font-semibold">Preview and read zone</h2>
          {snapshot && (
            <button type="button" className="text-sm text-purple-600 dark:text-purple-400 hover:underline" onClick={() => setRoiEditing(!roiEditing)}>
              {roiEditing ? 'Done' : 'Edit read zone'}
            </button>
          )}
        </div>

        {test.state === 'idle' && (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Run <strong>Test connection</strong> to check the camera and get a snapshot. The read zone is drawn on that snapshot.
          </p>
        )}
        {test.state === 'testing' && (
          <div className="flex items-center gap-3 text-sm text-gray-500 dark:text-gray-400" role="status">
            <svg className="animate-spin w-5 h-5 text-purple-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Contacting the camera (up to 10 seconds)…
          </div>
        )}
        {test.state === 'error' && <Alert>{test.message}</Alert>}
        {test.state === 'done' && <CameraTestResult result={test.result} />}

        {snapshot ? (
          <>
            <RoiPicker image={snapshot} roi={values.roi} onChange={(roi) => set('roi', roi)} editing={roiEditing} />
            {roiEditing && (
              <p className="text-xs text-gray-500 dark:text-gray-400">
                Drag a rectangle around the spot where the plate is when a vehicle stops at the barrier. Save the camera to apply it.
              </p>
            )}
          </>
        ) : values.roi ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            Read zone: x {values.roi.x} · y {values.roi.y} · w {values.roi.w} · h {values.roi.h}. Test the connection to see it on the image.
          </p>
        ) : null}
        {serverErrors.roi?.length ? <Alert>{serverErrors.roi.join(' ')}</Alert> : null}
      </div>
    </form>
  );
}
