'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import ThemeToggle from './ThemeToggle';
import HealthIndicator from './HealthIndicator';
import { useAuth } from './AuthContext';
import type { Role } from '@/lib/gate-api';

interface NavItem {
  href: string;
  label: string;
  role?: Role;
  /** Hidden from visitors who are not signed in. */
  authOnly?: boolean;
  external?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { href: '/', label: 'Home' },
  { href: '/images', label: 'Images', authOnly: true },
  { href: '/monitor', label: 'Monitor', role: 'gate_operator' },
  { href: '/gate', label: 'Gate', role: 'gate_operator' },
  { href: '/vehicles', label: 'Vehicles', role: 'gate_operator' },
  { href: '/events', label: 'Events', role: 'gate_operator' },
  { href: '/manage/cameras', label: 'Cameras', role: 'gate_admin' },
  { href: '/manage/gates', label: 'Gates', role: 'gate_admin' },
  { href: '/health', label: 'Health', external: true },
];

const linkClass = 'px-3 py-2 rounded-md text-sm font-medium hover:bg-gray-700 transition-colors';

export default function Navbar() {
  const [menuOpen, setMenuOpen] = useState(false);
  const { authenticated, username, hasRole, logout } = useAuth();
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
      <a key={item.href} href={item.href} target="_blank" rel="noopener noreferrer" className={cls}>{item.label}</a>
    ) : (
      <Link key={item.href} href={item.href} onClick={() => setMenuOpen(false)} className={cls}>{item.label}</Link>
    );
  };

  const account = authenticated ? (
    <>
      <span className="text-sm text-gray-300 truncate max-w-[10rem]" title={username ?? ''}>{username}</span>
      <button onClick={handleLogout} className={linkClass}>Log out</button>
    </>
  ) : (
    <Link href="/login" onClick={() => setMenuOpen(false)} className={linkClass}>Log in</Link>
  );

  return (
    <nav className="bg-gray-800 dark:bg-[#0f0f0f] text-white shadow-lg sticky top-0 z-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16">
          <div className="flex items-center space-x-8">
            <Link href="/" className="text-xl font-semibold tracking-tight">
              VietinBankSchool LPR
            </Link>
            <div className="hidden md:flex items-center space-x-1">
              {items.map((item) => renderLink(item, false))}
            </div>
          </div>
          <div className="flex items-center space-x-2">
            <div className="hidden md:flex items-center space-x-2">{account}</div>
            <HealthIndicator />
            <ThemeToggle />
            <button
              onClick={() => setMenuOpen(!menuOpen)}
              className="md:hidden p-2 rounded-full hover:bg-gray-700 transition-colors"
              aria-label="Toggle menu"
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
