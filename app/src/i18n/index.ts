import { createContext, useContext } from 'react'
import { en, type Key } from './en'
import { hi } from './hi'

export type Lang = 'en' | 'hi'
export const LANGS: { code: Lang; label: string }[] = [{ code: 'en', label: 'English' }, { code: 'hi', label: 'हिन्दी' }]
const dict: Record<Lang, Record<Key, string>> = { en, hi }

export type TFn = (key: Key, params?: Record<string, string | number>) => string

export function makeT(lang: Lang): TFn {
  return (key, params) => {
    let s = dict[lang][key] ?? en[key] ?? key
    if (params) for (const [k, v] of Object.entries(params)) s = s.replaceAll(`{${k}}`, String(v))
    return s
  }
}

export function detectLang(saved: string | null, nav: string | undefined): Lang {
  if (saved === 'en' || saved === 'hi') return saved
  return nav?.toLowerCase().startsWith('hi') ? 'hi' : 'en'
}

export const I18nContext = createContext<{ lang: Lang; setLang: (l: Lang) => void; t: TFn }>({
  lang: 'en', setLang: () => undefined, t: makeT('en'),
})
export const useT = () => useContext(I18nContext)
export type { Key }
