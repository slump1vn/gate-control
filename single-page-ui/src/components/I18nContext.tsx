'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import { dictionaries, en } from '@/lib/i18n/dictionaries';
import type { Dictionary, Language } from '@/lib/i18n/dictionaries';

const STORAGE_KEY = 'lpr-lang';
export const DEFAULT_LANGUAGE: Language = 'vi';

export type Translate = (key: keyof Dictionary, values?: Record<string, string | number>) => string;

interface I18nState {
  lang: Language;
  setLang: (lang: Language) => void;
  t: Translate;
}

/** "{n} frames" + {n: 3} → "3 frames". */
function format(template: string, values?: Record<string, string | number>): string {
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (match, name) => (
    name in values ? String(values[name]) : match
  ));
}

export function translator(lang: Language): Translate {
  const dictionary = dictionaries[lang] ?? en;
  // English is the fallback: a key added in one language still reads as words.
  return (key, values) => format(dictionary[key] ?? en[key] ?? String(key), values);
}

const I18nContext = createContext<I18nState>({
  lang: DEFAULT_LANGUAGE,
  setLang: () => {},
  t: translator(DEFAULT_LANGUAGE),
});

export function useI18n(): I18nState {
  return useContext(I18nContext);
}

export function I18nProvider({ children, initial }: { children: ReactNode; initial?: Language }) {
  const [lang, setLangState] = useState<Language>(initial ?? DEFAULT_LANGUAGE);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      // Reading the stored preference before mount would make the server HTML
      // and the first client render disagree.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (stored === 'vi' || stored === 'en') setLangState(stored);
    } catch { /* private mode or blocked storage: keep the default */ }
  }, []);

  const setLang = useCallback((next: Language) => {
    setLangState(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* not worth failing over */ }
    if (typeof document !== 'undefined') document.documentElement.lang = next;
  }, []);

  useEffect(() => {
    if (typeof document !== 'undefined') document.documentElement.lang = lang;
  }, [lang]);

  const value = useMemo<I18nState>(() => ({ lang, setLang, t: translator(lang) }), [lang, setLang]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
