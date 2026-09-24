'use client';

import { FormEvent, useState } from 'react';
import { ApiError } from '@/lib/gate-api';
import type { AppUser, Role, UserInput } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import { Alert, Checkbox, Field, inputClass, primaryButton, secondaryButton } from './ui';

interface UserFormProps {
  user?: AppUser | null;
  /** True while editing the account the caller is signed in as: role/active are locked. */
  isSelf?: boolean;
  onSubmit: (data: UserInput) => Promise<void>;
  onCancel: () => void;
}

const EMPTY: UserInput = {
  username: '',
  email: '',
  role: 'gate_operator',
  is_active: true,
};

export default function UserForm({ user, isSelf = false, onSubmit, onCancel }: UserFormProps) {
  const { t } = useI18n();
  const [data, setData] = useState<UserInput>(() => (user
    ? { username: user.username, email: user.email, role: (user.role ?? 'gate_operator') as Role, is_active: user.is_active }
    : EMPTY));
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [formError, setFormError] = useState('');
  const [saving, setSaving] = useState(false);

  const set = <K extends keyof UserInput>(key: K, value: UserInput[K]) => setData((d) => ({ ...d, [key]: value }));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErrors({});
    setFormError('');
    setSaving(true);
    try {
      await onSubmit({ ...data, ...(password ? { password } : {}) });
      setPassword('');
    } catch (err) {
      if (err instanceof ApiError && Object.keys(err.fieldErrors).length) {
        const { __all__, ...fields } = err.fieldErrors;
        setErrors(fields);
        if (__all__) setFormError(__all__.join(' '));
      } else {
        setFormError(err instanceof Error ? err.message : t('users.saveFailed'));
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} className="space-y-4">
      {formError && <Alert>{formError}</Alert>}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label={t('users.username')} htmlFor="user-username" error={errors.username}>
          <input id="user-username" className={inputClass} required autoFocus={!user}
            value={data.username} onChange={(e) => set('username', e.target.value)} />
        </Field>
        <Field label={t('users.email')} htmlFor="user-email" error={errors.email}>
          <input id="user-email" type="email" className={inputClass} value={data.email} onChange={(e) => set('email', e.target.value)} />
        </Field>
        <Field label={t('users.role')} htmlFor="user-role" error={errors.role} hint={isSelf ? t('users.selfRoleHint') : undefined}>
          <select id="user-role" className={inputClass} value={data.role} disabled={isSelf}
            onChange={(e) => set('role', e.target.value as Role)}>
            <option value="gate_admin">{t('users.roleAdmin')}</option>
            <option value="gate_operator">{t('users.roleOperator')}</option>
          </select>
        </Field>
        <Field
          label={t('users.password')}
          htmlFor="user-password"
          error={errors.password}
          hint={user ? t('users.passwordKeep') : t('users.passwordRequired')}
        >
          <input id="user-password" type="password" className={inputClass} autoComplete="new-password"
            value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div className="flex items-end pb-2">
          <Checkbox id="user-active" label={t('users.active')} checked={data.is_active} disabled={isSelf}
            onChange={(v) => set('is_active', v)} hint={isSelf ? t('users.selfActiveHint') : undefined} />
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className={secondaryButton} onClick={onCancel}>{t('common.cancel')}</button>
        <button type="submit" className={primaryButton} disabled={saving}>
          {saving ? t('common.saving') : t(user ? 'users.submitEdit' : 'users.submitNew')}
        </button>
      </div>
    </form>
  );
}
