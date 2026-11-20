import { BandClient, type Fetch } from './client'
import type { PairInfo } from './types'

export const DEFAULT_PORT = 8080

/** Turn whatever the user typed or the QR contained into a base URL, or null if it is not an address. */
export function parsePairText(raw: string): string | null {
  let s = raw.trim()
  if (!s || s.length > 200 || /\s/.test(s)) return null
  const m = /^senseband:\/\/(.+)$/i.exec(s)
  if (m?.[1]) s = m[1]
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(s) && !/^https?:\/\//i.test(s)) return null   // ftp://, file://, javascript:// ...
  if (!/^https?:\/\//i.test(s)) s = 'http://' + s
  let u: URL
  try { u = new URL(s) } catch { return null }
  if (!/^https?:$/.test(u.protocol)) return null
  const host = u.hostname
  const looksLikeHost = /^(\d{1,3}\.){3}\d{1,3}$/.test(host) || /^[a-z0-9-]+(\.[a-z0-9-]+)*$/i.test(host) || host.startsWith('[')
  if (!looksLikeHost) return null
  if (/^(\d{1,3}\.){3}\d{1,3}$/.test(host) && host.split('.').some((p) => Number(p) > 255)) return null
  const port = u.port || String(DEFAULT_PORT)
  return `${u.protocol}//${host}:${port}`
}

export interface Found { url: string; info: PairInfo }

/** Probe candidate addresses in parallel; the first that answers /api/pair wins. */
export async function discover(candidates: string[], fetchImpl?: Fetch): Promise<Found | null> {
  const uniq = [...new Set(candidates.filter(Boolean))]
  if (!uniq.length) return null
  const probes = uniq.map(async (url) => {
    const info = await new BandClient(url, fetchImpl).pair()
    if (info.api !== 1) throw new Error('unsupported api')
    return { url, info }
  })
  try { return await Promise.any(probes) } catch { return null }
}

/** Where a band is likely to be: same origin (app served by the band), the last one used, mDNS name, hotspot IP. */
export function defaultCandidates(saved: string | null, origin: string | null): string[] {
  const out: string[] = []
  if (origin && /^https?:/.test(origin)) out.push(origin)
  if (saved) out.push(saved)
  out.push(`http://senseband.local:${DEFAULT_PORT}`, `http://192.168.4.1:${DEFAULT_PORT}`)
  return out
}
