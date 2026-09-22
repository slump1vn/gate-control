'use client';

import { FormEvent, useEffect, useState } from 'react';
import { ApiError, previewPlate } from '@/lib/gate-api';
import type { PlatePreview, Vehicle, VehicleInput } from '@/lib/gate-api';
import { Alert, Checkbox, Field, inputClass, isoToLocalInput, localInputToIso, primaryButton, secondaryButton } from './ui';

interface VehicleFormProps {
  vehicle?: Vehicle | null;
  onSubmit: (data: VehicleInput) => Promise<void>;
  onCancel: () => void;
  /** Injected for Storybook; defaults to the API. */
  preview?: (plate: string, signal?: AbortSignal) => Promise<PlatePreview>;
}

const EMPTY: VehicleInput = {
  plate_display: '',
  owner_name: '',
  owner_phone: '',
  department: '',
  vehicle_type: 'car',
  valid_from: null,
  valid_until: null,
  is_active: true,
  notes: '',
};

export default function VehicleForm({ vehicle, onSubmit, onCancel, preview = previewPlate }: VehicleFormProps) {
  const [data, setData] = useState<VehicleInput>(() => {
    if (!vehicle) return EMPTY;
    const keys = Object.keys(EMPTY) as (keyof VehicleInput)[];
    return Object.fromEntries(keys.map((k) => [k, vehicle[k]])) as unknown as VehicleInput;
  });
  const [validFrom, setValidFrom] = useState(isoToLocalInput(vehicle?.valid_from));
  const [validUntil, setValidUntil] = useState(isoToLocalInput(vehicle?.valid_until));
  const [platePreview, setPlatePreview] = useState<PlatePreview | null>(null);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);

  // Live preview of how the registry will store the plate, debounced.
  useEffect(() => {
    const plate = data.plate_display.trim();
    if (!plate) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      preview(plate, controller.signal).then(setPlatePreview).catch(() => {});
    }, 250);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [data.plate_display, preview]);

  // A preview for text no longer in the field is stale.
  const shownPreview = platePreview && platePreview.plate === data.plate_display.trim() ? platePreview : null;

  const set = <K extends keyof VehicleInput>(key: K, value: VehicleInput[K]) => setData((d) => ({ ...d, [key]: value }));

  const duplicate = shownPreview?.existing_vehicle && shownPreview.existing_vehicle.id !== vehicle?.id
    ? shownPreview.existing_vehicle : null;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErrors({});
    setFormError('');
    if (validFrom && validUntil && validFrom >= validUntil) {
      setErrors({ valid_until: ['Must be after "Valid from".'] });
      return;
    }
    setSaving(true);
    try {
      await onSubmit({ ...data, valid_from: localInputToIso(validFrom), valid_until: localInputToIso(validUntil) });
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
        <Field
          label="Plate number"
          htmlFor="vehicle-plate"
          error={errors.plate_display}
          hint={shownPreview?.normalized
            ? <>Stored and matched as <span className="font-mono font-semibold text-gray-800 dark:text-gray-200">{shownPreview.normalized}</span></>
            : 'e.g. 30A-123.45 or 29B1 234.56'}
        >
          <input id="vehicle-plate" className={`${inputClass} font-mono`} required autoFocus
            value={data.plate_display} onChange={(e) => set('plate_display', e.target.value.toUpperCase())} />
          {duplicate && (
            <p className="mt-1 text-xs text-yellow-700 dark:text-yellow-400">
              Already registered as {duplicate.plate_display}.
            </p>
          )}
        </Field>
        <Field label="Vehicle type" htmlFor="vehicle-type" error={errors.vehicle_type}>
          <select id="vehicle-type" className={inputClass} value={data.vehicle_type}
            onChange={(e) => set('vehicle_type', e.target.value as VehicleInput['vehicle_type'])}>
            <option value="car">Car</option>
            <option value="motorbike">Motorbike</option>
            <option value="other">Other</option>
          </select>
        </Field>
        <Field label="Owner" htmlFor="vehicle-owner" error={errors.owner_name}>
          <input id="vehicle-owner" className={inputClass} required value={data.owner_name} onChange={(e) => set('owner_name', e.target.value)} />
        </Field>
        <Field label="Phone" htmlFor="vehicle-phone" error={errors.owner_phone}>
          <input id="vehicle-phone" className={inputClass} type="tel" value={data.owner_phone} onChange={(e) => set('owner_phone', e.target.value)} />
        </Field>
        <Field label="Department" htmlFor="vehicle-department" error={errors.department}>
          <input id="vehicle-department" className={inputClass} value={data.department} onChange={(e) => set('department', e.target.value)} />
        </Field>
        <div className="flex items-end pb-2">
          <Checkbox id="vehicle-active" label="Active" checked={data.is_active} onChange={(v) => set('is_active', v)}
            hint="Inactive vehicles are recognised but never let in." />
        </div>
        <Field label="Valid from" htmlFor="vehicle-from" error={errors.valid_from} hint="Empty = no start date">
          <input id="vehicle-from" type="datetime-local" className={inputClass} value={validFrom} onChange={(e) => setValidFrom(e.target.value)} />
        </Field>
        <Field label="Valid until" htmlFor="vehicle-until" error={errors.valid_until} hint="Empty = no end date">
          <input id="vehicle-until" type="datetime-local" className={inputClass} value={validUntil} onChange={(e) => setValidUntil(e.target.value)} />
        </Field>
      </div>
      <Field label="Notes" htmlFor="vehicle-notes" error={errors.notes}>
        <textarea id="vehicle-notes" rows={2} className={inputClass} value={data.notes} onChange={(e) => set('notes', e.target.value)} />
      </Field>
      <div className="flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onCancel}>Cancel</button>
        <button type="submit" className={primaryButton} disabled={saving}>{saving ? 'Saving…' : vehicle ? 'Save changes' : 'Add vehicle'}</button>
      </div>
    </form>
  );
}
