// Small shared building blocks for the gate pages, in the existing visual style.

import { ReactNode } from 'react';

export const inputClass =
  'w-full px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-[#1a1a1a] focus:outline-none focus:ring-2 focus:ring-purple-500 disabled:opacity-60';

export const primaryButton =
  'px-4 py-2 text-sm bg-gradient-to-r from-[#667eea] to-[#764ba2] text-white rounded-lg font-medium hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed';

export const secondaryButton =
  'px-4 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-[#1a1a1a] text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-[#2d2d2d] transition-colors disabled:opacity-50 disabled:cursor-not-allowed';

export const dangerButton =
  'px-4 py-2 text-sm rounded-lg bg-red-600 text-white font-medium hover:bg-red-700 transition-colors disabled:opacity-50 disabled:cursor-not-allowed';

export const cardClass =
  'bg-white dark:bg-[#1a1a1a] border border-gray-200 dark:border-gray-700 rounded-xl';

export function Field({ label, htmlFor, error, hint, children }: {
  label: string;
  htmlFor?: string;
  error?: string | string[] | null;
  hint?: ReactNode;
  children: ReactNode;
}) {
  const messages = Array.isArray(error) ? error : error ? [error] : [];
  return (
    <div>
      <label htmlFor={htmlFor} className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{label}</label>
      {children}
      {hint && messages.length === 0 && <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
      {messages.map((m) => (
        <p key={m} className="mt-1 text-xs text-red-600 dark:text-red-400">{m}</p>
      ))}
    </div>
  );
}

export function Checkbox({ id, label, checked, onChange, hint }: {
  id: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: ReactNode;
}) {
  return (
    <div>
      <label htmlFor={id} className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
        <input id={id} type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} className="w-4 h-4 accent-purple-600" />
        {label}
      </label>
      {hint && <p className="mt-1 ml-6 text-xs text-gray-500 dark:text-gray-400">{hint}</p>}
    </div>
  );
}

export function Alert({ kind = 'error', children }: { kind?: 'error' | 'success' | 'info' | 'warning'; children: ReactNode }) {
  const styles = {
    error: 'bg-red-100 dark:bg-red-900/30 border-red-300 dark:border-red-700 text-red-700 dark:text-red-400',
    success: 'bg-green-100 dark:bg-green-900/30 border-green-300 dark:border-green-700 text-green-700 dark:text-green-400',
    info: 'bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800 text-blue-700 dark:text-blue-300',
    warning: 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-300 dark:border-yellow-700 text-yellow-800 dark:text-yellow-300',
  }[kind];
  return <div role={kind === 'error' ? 'alert' : 'status'} className={`p-3 border rounded-lg text-sm ${styles}`}>{children}</div>;
}

const badgeColors = {
  green: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  red: 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-400',
  yellow: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  blue: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  gray: 'bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
  purple: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-300',
};

export type BadgeColor = keyof typeof badgeColors;

export function Badge({ color = 'gray', children, title }: { color?: BadgeColor; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${badgeColors[color]}`}>
      {children}
    </span>
  );
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
      <h1 className="text-2xl font-semibold">{title}</h1>
      {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-[60] flex items-start sm:items-center justify-center bg-black/50 p-4 overflow-y-auto" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`${cardClass} w-full max-w-2xl p-6 shadow-xl my-8`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}
      >
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="p-1 rounded hover:bg-gray-100 dark:hover:bg-[#2d2d2d]">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

/** "2026-09-22T08:30:00+07:00" → local "22/09/2026, 08:30:00". */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleString();
}

/** ISO timestamp → value for an <input type="datetime-local"> in the browser's zone. */
export function isoToLocalInput(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** datetime-local value → ISO in UTC, so the server does not guess the zone. */
export function localInputToIso(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return isNaN(d.getTime()) ? null : d.toISOString();
}
