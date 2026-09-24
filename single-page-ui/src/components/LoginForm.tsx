'use client';

import { FormEvent, useState } from 'react';
import { ApiError } from '@/lib/gate-api';
import { useI18n } from './I18nContext';
import { Alert, Field, cardClass, inputClass, primaryButton } from './ui';

interface LoginFormProps {
  onLogin: (username: string, password: string) => Promise<void>;
  initialError?: string;
}

export default function LoginForm({ onLogin, initialError = '' }: LoginFormProps) {
  const { t } = useI18n();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(initialError);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await onLogin(username.trim(), password);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : t('auth.failed'));
      setPassword('');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} className={`${cardClass} p-6 space-y-4`}>
      <h1 className="text-xl font-semibold">{t('auth.signIn')}</h1>
      <p className="text-sm text-gray-500 dark:text-gray-400">{t('auth.intro')}</p>
      {error && <Alert>{error}</Alert>}
      <Field label={t('auth.username')} htmlFor="login-username">
        <input id="login-username" className={inputClass} autoComplete="username" autoFocus required
          value={username} onChange={(e) => setUsername(e.target.value)} />
      </Field>
      <Field label={t('auth.password')} htmlFor="login-password">
        <input id="login-password" type="password" className={inputClass} autoComplete="current-password" required
          value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <button type="submit" disabled={submitting || !username || !password} className={`${primaryButton} w-full`}>
        {t(submitting ? 'auth.signingIn' : 'auth.signIn')}
      </button>
    </form>
  );
}
