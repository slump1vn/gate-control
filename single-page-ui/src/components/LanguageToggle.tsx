'use client';

import { useI18n } from './I18nContext';

/** Two letters, so a guard can switch back if the page opens in the wrong language. */
export default function LanguageToggle() {
  const { lang, setLang, t } = useI18n();
  const next = lang === 'vi' ? 'en' : 'vi';

  return (
    <button
      onClick={() => setLang(next)}
      title={`${t('lang.switch')}: ${t(next === 'vi' ? 'lang.vi' : 'lang.en')}`}
      aria-label={`${t('lang.switch')}: ${t(next === 'vi' ? 'lang.vi' : 'lang.en')}`}
      className="px-2 py-1 rounded-md text-sm font-semibold tracking-wide hover:bg-gray-700 transition-colors"
    >
      {lang.toUpperCase()}
    </button>
  );
}
