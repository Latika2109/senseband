/// <reference lib="webworker" />
// Service worker: offline app shell + web push notifications for alerts (Milestone 4).
import { clientsClaim } from 'workbox-core'
import { cleanupOutdatedCaches, precacheAndRoute } from 'workbox-precaching'

declare const self: ServiceWorkerGlobalScope & { __WB_MANIFEST: { url: string; revision: string | null }[] }
void self.skipWaiting()
clientsClaim()
cleanupOutdatedCaches()
precacheAndRoute(self.__WB_MANIFEST)          // live data (/api, /ws, Supabase) is never cached

self.addEventListener('push', (event) => {
  let n: { title?: string; body?: string; tag?: string; url?: string; requireInteraction?: boolean } = {}
  try { n = event.data?.json() ?? {} } catch { n = { title: 'SenseBand', body: event.data?.text() } }
  event.waitUntil(self.registration.showNotification(n.title ?? 'SenseBand', {
    body: n.body, tag: n.tag, requireInteraction: !!n.requireInteraction, data: { url: n.url ?? '/#/alerts' }, icon: 'icon-192.png', badge: 'icon-192.png',
  }))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = (event.notification.data as { url?: string } | undefined)?.url ?? '/'
  event.waitUntil(self.clients.matchAll({ type: 'window' }).then((all) => {
    const open = all.find((c) => 'focus' in c)
    return open ? open.focus().then(() => open.navigate(url)) : self.clients.openWindow(url)
  }))
})
