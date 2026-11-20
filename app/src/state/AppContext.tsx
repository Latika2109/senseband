import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from 'react'
import { BandClient } from '../api/client'
import { defaultCandidates, discover } from '../api/discovery'
import { initialLive, LiveConnection, liveReducer, type LiveState } from '../api/live'
import { load, remove, save } from '../api/storage'
import type { PairInfo } from '../api/types'
import { detectLang, I18nContext, makeT, type Lang } from '../i18n'
import { emptyProfile, toPatientPayload, type ProfileDraft } from './profile'
import { emptyRecovery, type RecoveryDraft } from './recovery'

export interface Persisted {
  profileDone: boolean; profile: ProfileDraft; bandUrl: string | null
  programType: 'monitoring' | 'recovery' | null; purpose: string | null
  durationChosen: boolean; durationH: number | null; wearDone: boolean; started: boolean
  recovery: RecoveryDraft
}
const initialPersisted: Persisted = {
  profileDone: false, profile: emptyProfile(), bandUrl: null, programType: null, purpose: null,
  durationChosen: false, durationH: null, wearDone: false, started: false, recovery: emptyRecovery(),
}

interface Ctx {
  p: Persisted; setP: (patch: Partial<Persisted>) => void
  client: BandClient | null; live: LiveState; pairInfo: PairInfo | null
  connect: (url: string) => Promise<PairInfo>
  autoDiscover: () => Promise<{ url: string; info: PairInfo } | null>
  pushProfile: () => Promise<void>
  resetSetup: () => void; forget: () => void; resetRehab: () => void
  now: number
}
const AppCtx = createContext<Ctx | null>(null)
export const useApp = () => {
  const c = useContext(AppCtx)
  if (!c) throw new Error('useApp outside provider')
  return c
}

export function AppProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => detectLang(load<string | null>('lang', null), navigator.language))
  const [p, setPState] = useState<Persisted>(() => ({ ...initialPersisted, ...load<Partial<Persisted>>('state', {}) }))
  const [live, dispatch] = useReducer(liveReducer, initialLive)
  const [pairInfo, setPairInfo] = useState<PairInfo | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const conn = useRef<LiveConnection | null>(null)

  const setLang = useCallback((l: Lang) => { setLangState(l); save('lang', l); document.documentElement.lang = l }, [])
  const setP = useCallback((patch: Partial<Persisted>) => {
    setPState((prev) => { const n = { ...prev, ...patch }; save('state', n); return n })
  }, [])
  useEffect(() => { document.documentElement.lang = lang }, [lang])

  const client = useMemo(() => (p.bandUrl ? new BandClient(p.bandUrl) : null), [p.bandUrl])

  // 4 Hz clock only drives countdown displays
  useEffect(() => { const i = setInterval(() => setNow(Date.now()), 250); return () => clearInterval(i) }, [])

  useEffect(() => {
    conn.current?.stop()
    dispatch({ type: 'reset' })
    if (!client) return
    const c = new LiveConnection(client.wsUrl(), {
      msg: (msg) => dispatch({ type: 'msg', msg, now: Date.now() }),
      conn: (cn) => dispatch({ type: 'conn', conn: cn }),
    })
    conn.current = c
    c.start()
    client.pair().then(setPairInfo).catch(() => undefined)
    return () => c.stop()
  }, [client])

  const connect = useCallback(async (url: string) => {
    const c = new BandClient(url)
    const info = await c.pair()
    setPairInfo(info)
    setP({ bandUrl: c.baseUrl })
    return info
  }, [setP])

  const autoDiscover = useCallback(async () => {
    const origin = typeof location !== 'undefined' ? location.origin : null
    const found = await discover(defaultCandidates(p.bandUrl, origin))
    if (found) setPairInfo(found.info)
    return found
  }, [p.bandUrl])

  const pushProfile = useCallback(async () => {
    if (client && p.profileDone) await client.putPatient(toPatientPayload(p.profile))
  }, [client, p.profile, p.profileDone])

  const resetSetup = useCallback(() => {
    setP({ programType: null, purpose: null, durationChosen: false, durationH: null, wearDone: false, started: false, recovery: emptyRecovery() })
  }, [setP])
  const forget = useCallback(() => { remove('state'); setPState(initialPersisted); setPairInfo(null) }, [])

  const resetRehab = useCallback(() => dispatch({ type: 'rehab_reset' }), [])

  const i18n = useMemo(() => ({ lang, setLang, t: makeT(lang) }), [lang, setLang])
  const value = useMemo(() => ({ p, setP, client, live, pairInfo, connect, autoDiscover, pushProfile, resetSetup, forget, resetRehab, now }),
    [p, setP, client, live, pairInfo, connect, autoDiscover, pushProfile, resetSetup, forget, resetRehab, now])
  return <I18nContext.Provider value={i18n}><AppCtx.Provider value={value}>{children}</AppCtx.Provider></I18nContext.Provider>
}
