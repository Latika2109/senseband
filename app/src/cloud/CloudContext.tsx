import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { cloudConfig, createSupabaseApi } from './supabase'
import type { CloudApi, CloudEvent, Profile } from './types'
import { vapidKey } from './logic'

interface Ctx {
  api: CloudApi | null; configured: boolean; ready: boolean; userId: string | null; profile: Profile | null
  refresh: () => Promise<void>; signOut: () => Promise<void>
  toasts: CloudEvent[]; dismiss: (id: string) => void
  enablePush: () => Promise<'on' | 'denied' | 'unsupported'>
}
const C = createContext<Ctx>({ api: null, configured: false, ready: true, userId: null, profile: null, refresh: async () => undefined, signOut: async () => undefined, toasts: [], dismiss: () => undefined, enablePush: async () => 'unsupported' })
export const useCloud = () => useContext(C)

/** `api` can be injected (tests); otherwise Supabase is used when configured. */
export function CloudProvider({ children, api: injected }: { children: ReactNode; api?: CloudApi | null }) {
  const cfg = useMemo(() => (injected ? null : cloudConfig()), [injected])
  const api = useMemo(() => injected ?? (cfg ? createSupabaseApi(cfg) : null), [injected, cfg])
  const [userId, setUserId] = useState<string | null>(null)
  const [profile, setProfile] = useState<Profile | null>(null)
  const [ready, setReady] = useState(!api)
  const [toasts, setToasts] = useState<CloudEvent[]>([])

  const refresh = useCallback(async () => {
    if (!api) { setReady(true); return }
    try {
      const uid = await api.userId()
      setUserId(uid)
      setProfile(uid ? await api.profile() : null)
    } catch { setUserId(null); setProfile(null) } finally { setReady(true) }
  }, [api])
  useEffect(() => { void refresh() }, [refresh])

  // Realtime: new warnings/criticals appear instantly (and as a system notification if the tab is hidden)
  useEffect(() => {
    if (!api || !userId) return
    return api.subscribeEvents((e) => {
      if (!e || e.level === 'info' || e.status === 'cancelled') return
      setToasts((t) => [e, ...t.filter((x) => x.id !== e.id)].slice(0, 3))
      if (typeof document !== 'undefined' && document.hidden && 'Notification' in window && Notification.permission === 'granted') {
        new Notification(e.level === 'critical' ? 'SenseBand ALERT' : 'SenseBand warning', { body: e.message ?? e.kind, tag: `event-${e.id}` })
      }
    })
  }, [api, userId])

  const enablePush = useCallback(async () => {
    if (!api || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return 'unsupported' as const
    const perm = await Notification.requestPermission()
    if (perm !== 'granted') return 'denied' as const
    const key = (import.meta.env as Record<string, string | undefined>).VITE_VAPID_PUBLIC_KEY ?? cfg?.vapidPublicKey
    if (!key) return 'unsupported' as const
    const reg = await navigator.serviceWorker.ready
    const sub = (await reg.pushManager.getSubscription()) ?? await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKey(key) as BufferSource })
    const j = sub.toJSON() as { endpoint: string; keys: { p256dh: string; auth: string } }
    await api.savePush({ endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth })
    return 'on' as const
  }, [api, cfg])

  const signOut = useCallback(async () => { await api?.signOut(); setUserId(null); setProfile(null) }, [api])
  const dismiss = useCallback((id: string) => setToasts((t) => t.filter((x) => x.id !== id)), [])
  const value = useMemo(() => ({ api, configured: !!api, ready, userId, profile, refresh, signOut, toasts, dismiss, enablePush }),
    [api, ready, userId, profile, refresh, signOut, toasts, dismiss, enablePush])
  return <C.Provider value={value}>{children}</C.Provider>
}
