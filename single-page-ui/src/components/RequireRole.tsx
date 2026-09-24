'use client';

import { useEffect, ReactNode } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from './AuthContext';
import { useI18n } from './I18nContext';
import type { Role } from '@/lib/gate-api';
import Spinner from './Spinner';

/**
 * Client-side route guard. It only decides what to render: the API enforces
 * the same roles on every call.
 */
export default function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { t } = useI18n();
  const { loading, authenticated, hasRole } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (!loading && !authenticated) {
      router.replace(`/login?next=${encodeURIComponent(pathname || '/')}`);
    }
  }, [loading, authenticated, router, pathname]);

  if (loading || !authenticated) {
    return <Spinner className="py-24" />;
  }

  if (!hasRole(role)) {
    return (
      <div className="max-w-xl mx-auto px-4 py-16 text-center">
        <p className="text-lg font-medium mb-2">{t('auth.noAccess')}</p>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">{t('auth.needsRole', { role })}</p>
        <Link href="/" className="text-purple-600 dark:text-purple-400 hover:underline">{t('common.back')}</Link>
      </div>
    );
  }

  return <>{children}</>;
}
