'use client';

import { Suspense, useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '@/components/AuthContext';
import LoginForm from '@/components/LoginForm';
import Spinner from '@/components/Spinner';

/** Only same-site paths, so ?next= cannot send the user to another site. */
function safeNext(next: string | null): string {
  if (next && next.startsWith('/') && !next.startsWith('//') && !next.startsWith('/\\')) return next;
  return '/gate';
}

function LoginContent() {
  const { loading, authenticated, login } = useAuth();
  const router = useRouter();
  const next = safeNext(useSearchParams().get('next'));

  useEffect(() => {
    if (!loading && authenticated) router.replace(next);
  }, [loading, authenticated, router, next]);

  if (loading || authenticated) return <Spinner className="py-24" />;

  return (
    <div className="max-w-sm mx-auto px-4 py-16">
      <LoginForm onLogin={login} />
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<Spinner className="py-24" />}>
      <LoginContent />
    </Suspense>
  );
}
