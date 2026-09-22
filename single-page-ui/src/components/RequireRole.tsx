'use client';

import { useEffect, ReactNode } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import { useAuth } from './AuthContext';
import type { Role } from '@/lib/gate-api';
import Spinner from './Spinner';

/**
 * Client-side route guard. It only decides what to render: the API enforces
 * the same roles on every call.
 */
export default function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
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
        <p className="text-lg font-medium mb-2">You do not have access to this page</p>
        <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
          It requires the <code className="px-1 rounded bg-gray-100 dark:bg-[#2d2d2d]">{role}</code> role. Ask an administrator to add you to that group.
        </p>
        <Link href="/" className="text-purple-600 dark:text-purple-400 hover:underline">Back to home</Link>
      </div>
    );
  }

  return <>{children}</>;
}
