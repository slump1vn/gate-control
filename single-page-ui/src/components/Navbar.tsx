'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ThemeToggle from './ThemeToggle';
import LanguageToggle from './LanguageToggle';
import HealthIndicator from './HealthIndicator';
import { useAuth } from './AuthContext';
import { useI18n } from './I18nContext';
import type { Dictionary } from '@/lib/i18n/dictionaries';
import type { Role } from '@/lib/gate-api';

interface NavItem {
  href: string;
  label: keyof Dictionary;
  role?: Role;
  /** Hidden from visitors who are not signed in. */
  authOnly?: boolean;
  external?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: '/', label: 'nav.home' },
  { href: '/images', label: 'nav.images', authOnly: true },
  { href: '/monitor', label: 'nav.monitor', role: 'gate_operator' },
  { href: '/gate', label: 'nav.gate', role: 'gate_operator' },
  { href: '/vehicles', label: 'nav.vehicles', role: 'gate_operator' },
  { href: '/events', label: 'nav.events', role: 'gate_operator' },
  { href: '/manage/cameras', label: 'nav.cameras', role: 'gate_admin' },
  { href: '/manage/gates', label: 'nav.gates', role: 'gate_admin' },
  { href: '/health', label: 'nav.health', external: true },
];

const linkClass = 'px-3 py-2 rounded-md text-sm font-medium hover:bg-gray-700 transition-colors';

export default function Navbar() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { authenticated, username, hasRole, logout } = useAuth();
  const { t } = useI18n();
  const router = useRouter();

  // Admin-only items are hidden from operators; the API enforces roles regardless.
  const items = NAV_ITEMS.filter((item) => (
    (!item.role || hasRole(item.role)) && (!item.authOnly || authenticated)
  ));

  const handleLogout = async () => {
    setMenuOpen(false);
    await logout();
    router.push('/');
  };

  const renderLink = (item: NavItem, mobile: boolean) => {
    const cls = mobile ? `block ${linkClass}` : linkClass;
    return item.external ? (
      <a key={item.href} href={item.href} target="_blank" rel="noopener noreferrer" className={cls}>{t(item.label)}</a>
    ) : (
      <Link key={item.href} href={item.href} onClick={() => setMenuOpen(false)} className={cls}>{t(item.label)}</Link>
    );
  };

  const account = authenticated ? (
    <>
      <span className="text-sm text-gray-300 truncate max-w-[10rem]" title={username ?? ''}>{username}</span>
      <button onClick={handleLogout} className={linkClass}>{t('nav.logout')}</button>
    </>
  ) : (
    <Link href="/login" onClick={() => setMenuOpen(false)} className={linkClass}>{t('nav.login')}</Link>
  );

  return (
    <nav className="bg-gray-800 dark:bg-[#0f0f0f] text-white shadow-lg sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <div className="flex items-center space-x-8 min-w-0">
            <Link href="/" className="text-base sm:text-xl font-semibold tracking-tight truncate">
              VietinBankSchool LPR
            </Link>
            <div className="hidden md:flex items-center space-x-1">
              {items.map((item) => renderLink(item, false))}
            </div>
          </div>
          <div className="flex items-center space-x-1 sm:space-x-2 shrink-0">
            <div className="hidden md:flex items-center space-x-2">{account}</div>
            <HealthIndicator />
            <LanguageToggle />
            <ThemeToggle />
            <button
              onClick={() => setMenuOpen(!menuOpen)}
              className="md:hidden p-2 rounded-full hover:bg-gray-700 transition-colors"
              aria-label={t('nav.menu')}
            >
              {menuOpen ? (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              )}
            </button>
          </div>
        </div>
      </div>
      {menuOpen && (
        <div className="md:hidden border-t border-gray-700">
          <div className="px-4 py-3 space-y-1">
            {items.map((item) => renderLink(item, true))}
            <div className="flex items-center justify-between pt-2 border-t border-gray-700">{account}</div>
          </div>
        </div>
      )}
    </nav>
  );
}
