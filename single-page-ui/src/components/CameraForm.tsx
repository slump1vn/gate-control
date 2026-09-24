'use client';

import { FormEvent, useEffect, useMemo, useState } from 'react';
import { ApiError, cameraSnapshotPath } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import type { Translate } from './I18nContext';
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
  /** A saved camera can be aimed against its live view instead of a still. */
  apiBase?: string;
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
  live_snapshot_path: '',
  prefer_snapshot: true,
  roi: null,
  motion_threshold: 0.02,
  settle_ms: 800,
  cooldown_s: 5,
};

const PATH_FIELDS = ['main_stream_path', 'sub_stream_path', 'snapshot_path', 'live_snapshot_path'] as const;

const LIVE_REFRESH_MS = 1000;

const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}$/;
const HOSTNAME = /^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$/;

/** Same rules as the server's validate_host_syntax; the network allowlist is checked server-side. */
export function hostError(host: string, t: Translate): string | null {
  const h = host.trim();
  if (!h) return t('cameraForm.hostRequired');
  if (/[:/@?\s\\]/.test(h)) return t('cameraForm.hostSyntax');
  if (/^[\d.]+$/.test(h)) return IPV4.test(h) ? null : t('cameraForm.hostNotIp', { host: h });
  return HOSTNAME.test(h) ? null : t('cameraForm.hostNotName', { host: h });
}

function toValues(camera: Camera | null | undefined, presets: CameraPresets): Values {
  if (!camera) return { ...DEFAULTS, ...presets[DEFAULTS.vendor] };
  const v = {} as Record<string, unknown>;
  (Object.keys(DEFAULTS) as (keyof Values)[]).forEach((k) => { v[k] = camera[k]; });
  return v as unknown as Values;
}

function clientErrors(v: Values, raw: Record<NumberField, string>, t: Translate): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!v.name.trim()) errors.name = t('cameraForm.nameRequired');
  const h = hostError(v.host, t);
  if (h) errors.host = h;
  for (const port of ['rtsp_port', 'http_port'] as const) {
    const n = Number(raw[port]);
    if (!/^\d+$/.test(raw[port]) || n < 1 || n > 65535) errors[port] = t('cameraForm.portRange');
  }
  const threshold = Number(raw.motion_threshold);
  if (raw.motion_threshold === '' || isNaN(threshold) || threshold < 0 || threshold > 1) {
    errors.motion_threshold = t('cameraForm.between01');
  }
  for (const f of ['settle_ms', 'cooldown_s'] as const) {
    if (!/^\d+$/.test(raw[f])) errors[f] = t('cameraForm.wholeNumber');
  }
  return errors;
}

export default function CameraForm({
  camera, presets, onSave, onTest, onCancel,
  initialTest = { state: 'idle' }, initialRoiEditing = false, apiBase,
}: CameraFormProps) {
  const { t } = useI18n();
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
  // Aiming a read zone is much easier against a moving picture: a vehicle can
  // drive in while the zone is drawn around where it comes to a stop.
  const [live, setLive] = useState(false);
  const [liveSrc, setLiveSrc] = useState('');
  const [dragging, setDragging] = useState(false);
  const canGoLive = !!camera && apiBase !== undefined;

  useEffect(() => {
    if (!live || !camera || dragging) return;
    const tick = () => setLiveSrc(`${apiBase ?? ''}${cameraSnapshotPath(camera.id)}?t=${Date.now()}`);
    tick();
    const timer = setInterval(tick, LIVE_REFRESH_MS);
    return () => clearInterval(timer);
  }, [live, camera, apiBase, dragging]);

  const current: Values = {
    ...values,
    rtsp_port: Number(raw.rtsp_port),
    http_port: Number(raw.http_port),
    motion_threshold: Number(raw.motion_threshold),
    settle_ms: Number(raw.settle_ms),
    cooldown_s: Number(raw.cooldown_s),
  };
  const errors = clientErrors(values, raw, t);
  const dirty = JSON.stringify(current) !== JSON.stringify(initial) || password !== '';
  const testSnapshot = test.state === 'done' ? test.result.image : null;
  const snapshot = live && liveSrc ? liveSrc : testSnapshot;

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
      setFormError([...(__all__ ?? []), ...roi].join(' ') || t('cameraForm.fixFields'));
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
          <Field label={t('cameraForm.name')} htmlFor="camera-name" error={errorFor('name')}>
            <input id="camera-name" className={inputClass} value={values.name}
              onChange={(e) => set('name', e.target.value)} onBlur={() => touch('name')} />
          </Field>
          <Field label={t('cameraForm.vendor')} htmlFor="camera-vendor" error={errorFor('vendor')} hint={t('cameraForm.vendorHint')}>
            <select id="camera-vendor" className={inputClass} value={values.vendor} onChange={(e) => changeVendor(e.target.value as Vendor)}>
              <option value="hikvision">Hikvision</option>
              <option value="dahua">Dahua</option>
              <option value="generic">{t('cameraForm.generic')}</option>
            </select>
          </Field>
          <Field label={t('cameraForm.host')} htmlFor="camera-host" error={errorFor('host')}
            hint={t('cameraForm.hostHint')}>
            <input id="camera-host" className={`${inputClass} font-mono`} value={values.host} placeholder="192.168.1.64"
              onChange={(e) => set('host', e.target.value.trim())} onBlur={() => touch('host')} aria-invalid={errorFor('host').length > 0} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label={t('cameraForm.rtspPort')} htmlFor="camera-rtsp_port" error={errorFor('rtsp_port')}>
              {numberInput('rtsp_port', { min: '1', max: '65535' })}
            </Field>
            <Field label={t('cameraForm.httpPort')} htmlFor="camera-http_port" error={errorFor('http_port')}>
              {numberInput('http_port', { min: '1', max: '65535' })}
            </Field>
          </div>
          <Field label={t('cameraForm.username')} htmlFor="camera-username" error={errorFor('username')}>
            <input id="camera-username" className={inputClass} autoComplete="off" value={values.username}
              onChange={(e) => set('username', e.target.value)} />
          </Field>
          <Field
            label={t('cameraForm.password')}
            htmlFor="camera-password"
            error={serverErrors.password}
            hint={camera?.password_set
              ? <>{t('cameraForm.passwordKeep')} <Badge color="green">{t('cameraForm.passwordSet')}</Badge></>
              : t('cameraForm.passwordNew')}
          >
            <input id="camera-password" type="password" className={inputClass} autoComplete="new-password"
              placeholder={camera?.password_set ? '••••••••' : ''} value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
        </div>

        <div className="space-y-3">
          {PATH_FIELDS.map((f) => (
            <Field
              key={f}
              label={t(({
                main_stream_path: 'cameraForm.mainPath',
                sub_stream_path: 'cameraForm.subPath',
                snapshot_path: 'cameraForm.snapshotPath',
                live_snapshot_path: 'cameraForm.livePath',
              } as Record<string, keyof Dictionary>)[f])}
              htmlFor={`camera-${f}`}
              error={errorFor(f)}
              hint={f === 'live_snapshot_path' ? t('cameraForm.livePathHint') : undefined}
            >
              <input id={`camera-${f}`} className={`${inputClass} font-mono text-xs`} value={values[f]}
                placeholder={presets[values.vendor]?.[f] || ''} onChange={(e) => set(f, e.target.value)} />
            </Field>
          ))}
        </div>

        <div className="flex flex-wrap gap-6">
          <Checkbox id="camera-prefer-snapshot" label={t('cameraForm.preferSnapshot')} checked={values.prefer_snapshot}
            onChange={(v) => set('prefer_snapshot', v)} hint={t('cameraForm.preferSnapshotHint')} />
          <Checkbox id="camera-enabled" label={t('cameraForm.enabled')} checked={values.is_enabled} onChange={(v) => set('is_enabled', v)} />
        </div>

        <div>
          <button type="button" onClick={() => setAdvanced(!advanced)} aria-expanded={advanced}
            className="text-sm text-purple-600 dark:text-purple-400 hover:underline">
            {advanced ? '▾' : '▸'} {t('cameraForm.advanced')}
          </button>
          {advanced && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mt-3">
              <Field label={t('cameraForm.motionThreshold')} htmlFor="camera-motion_threshold" error={errorFor('motion_threshold')}
                hint={t('cameraForm.motionThresholdHint')}>
                {numberInput('motion_threshold', { step: '0.005', min: '0', max: '1' })}
              </Field>
              <Field label={t('cameraForm.settle')} htmlFor="camera-settle_ms" error={errorFor('settle_ms')}
                hint={t('cameraForm.settleHint')}>
                {numberInput('settle_ms', { step: '100', min: '0' })}
              </Field>
              <Field label={t('cameraForm.cooldown')} htmlFor="camera-cooldown_s" error={errorFor('cooldown_s')}
                hint={t('cameraForm.cooldownHint')}>
                {numberInput('cooldown_s', { min: '0' })}
              </Field>
            </div>
          )}
        </div>

        <div className="flex flex-wrap justify-end gap-2 pt-2 border-t border-gray-200 dark:border-gray-700">
          {onCancel && <button type="button" className={secondaryButton} onClick={onCancel}>{t('common.cancel')}</button>}
          <button type="button" className={secondaryButton} onClick={runTest} disabled={test.state === 'testing'}>
            {t(test.state === 'testing' ? 'cameraForm.testing' : 'cameraForm.test')}
          </button>
          <button type="submit" className={primaryButton} disabled={saving}>
            {saving ? t('common.saving') : t(camera ? 'cameraForm.submitEdit' : 'cameraForm.submitNew')}
          </button>
        </div>
      </div>

      <div className={`${cardClass} p-5 space-y-4`}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="font-semibold">{t('cameraForm.preview')}</h2>
          {canGoLive && (
            <button type="button" onClick={() => setLive(!live)}
              className="text-sm text-purple-600 dark:text-purple-400 hover:underline">
              {t(live ? 'cameraForm.useStill' : 'cameraForm.useLive')}
            </button>
          )}
          {snapshot && (
            <button type="button" className="text-sm text-purple-600 dark:text-purple-400 hover:underline" onClick={() => setRoiEditing(!roiEditing)}>
              {t(roiEditing ? 'cameraForm.doneZone' : 'cameraForm.editZone')}
            </button>
          )}
        </div>

        {test.state === 'idle' && (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {t('cameraForm.previewHint')}
          </p>
        )}
        {test.state === 'testing' && (
          <div className="flex items-center gap-3 text-sm text-gray-500 dark:text-gray-400" role="status">
            <svg className="animate-spin w-5 h-5 text-purple-500" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            {t('cameraForm.contacting')}
          </div>
        )}
        {test.state === 'error' && <Alert>{test.message}</Alert>}
        {test.state === 'done' && <CameraTestResult result={test.result} />}

        {snapshot ? (
          <>
            <RoiPicker image={snapshot} roi={values.roi} onChange={(roi) => set('roi', roi)}
              editing={roiEditing} onDraggingChange={setDragging} />
            {roiEditing && (
              <p className="text-xs text-gray-500 dark:text-gray-400">{t('cameraForm.zoneHint')}</p>
            )}
          </>
        ) : values.roi ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">
            {t('cameraForm.zoneSaved', { x: values.roi.x, y: values.roi.y, w: values.roi.w, h: values.roi.h })}
          </p>
        ) : null}
        {serverErrors.roi?.length ? <Alert>{serverErrors.roi.join(' ')}</Alert> : null}
      </div>
    </form>
  );
}
