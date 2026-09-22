'use client';

import { FormEvent, useState } from 'react';
import { ApiError } from '@/lib/gate-api';
import { Alert, Field, cardClass, inputClass, primaryButton } from './ui';

interface LoginFormProps {
  onLogin: (username: string, password: string) => Promise<void>;
  initialError?: string;
}

export default function LoginForm({ onLogin, initialError = '' }: LoginFormProps) {
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
      setError(err instanceof ApiError || err instanceof Error ? err.message : 'Login failed');
      setPassword('');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} className={`${cardClass} p-6 space-y-4`}>
      <h1 className="text-xl font-semibold">Sign in</h1>
      <p className="text-sm text-gray-500 dark:text-gray-400">
        Gate operators and administrators sign in to manage vehicles, access events and cameras.
      </p>
      {error && <Alert>{error}</Alert>}
      <Field label="Username" htmlFor="login-username">
        <input id="login-username" className={inputClass} autoComplete="username" autoFocus required
          value={username} onChange={(e) => setUsername(e.target.value)} />
      </Field>
      <Field label="Password" htmlFor="login-password">
        <input id="login-password" type="password" className={inputClass} autoComplete="current-password" required
          value={password} onChange={(e) => setPassword(e.target.value)} />
      </Field>
      <button type="submit" disabled={submitting || !username || !password} className={`${primaryButton} w-full`}>
        {submitting ? 'Signing in…' : 'Sign in'}
      </button>
    </form>
  );
}
