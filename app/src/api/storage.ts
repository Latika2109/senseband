// localStorage can throw (private mode, blocked): every access is wrapped, the app still works without it.
export function load<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem('sb.' + key)
    return raw == null ? fallback : (JSON.parse(raw) as T)
  } catch { return fallback }
}
export function save(key: string, value: unknown): void {
  try { localStorage.setItem('sb.' + key, JSON.stringify(value)) } catch { /* ignore */ }
}
export function remove(key: string): void {
  try { localStorage.removeItem('sb.' + key) } catch { /* ignore */ }
}
