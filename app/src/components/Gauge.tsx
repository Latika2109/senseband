import { arcPath, needleDeg } from '../state/recovery'

/** Live angle dial: 0..max degrees on a half circle, with the target zone and the doctor's limit marked. */
export function Gauge({ angle, max, target, limit, inTarget, over, label }: { angle: number; max: number; target?: number | null; limit?: number | null; inTarget?: boolean; over?: boolean; label: string }) {
  const cx = 110, cy = 110, r = 90
  const needle = needleDeg(Math.max(0, angle), max)
  const tip = (deg: number, len: number) => { const a = Math.PI - (deg * Math.PI) / 180; return [cx + len * Math.cos(a), cy - len * Math.sin(a)] }
  const [nx, ny] = tip(needle, r - 8)
  const zoneFrom = target != null ? needleDeg(target * 0.9, max) : null
  const zoneTo = target != null ? needleDeg(limit ?? target * 1.1, max) : null
  return (
    <svg viewBox="0 0 220 130" role="img" aria-label={`${label}: ${Math.round(angle)}°`} className="mx-auto w-full max-w-sm">
      <path d={arcPath(cx, cy, r, 0, 180)} fill="none" stroke="rgb(var(--line))" strokeWidth="16" strokeLinecap="round" />
      {zoneFrom != null && zoneTo != null && <path d={arcPath(cx, cy, r, zoneFrom, Math.max(zoneTo, zoneFrom + 3))} fill="none" stroke={`rgb(var(--${inTarget ? 'ok' : 'brand'}))`} strokeWidth="16" opacity={inTarget ? 1 : 0.55} />}
      {limit != null && (() => { const [x1, y1] = tip(needleDeg(limit, max), r - 14); const [x2, y2] = tip(needleDeg(limit, max), r + 12); return <line x1={x1} y1={y1} x2={x2} y2={y2} stroke="rgb(var(--crit))" strokeWidth="3" /> })()}
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={`rgb(var(--${over ? 'crit' : 'ink'}))`} strokeWidth="4" strokeLinecap="round" />
      <circle cx={cx} cy={cy} r="7" fill={`rgb(var(--${over ? 'crit' : 'ink'}))`} />
      <text x={cx} y={cy - 28} textAnchor="middle" className="fill-current text-ink" fontSize="30" fontWeight="700">{Math.round(angle)}°</text>
    </svg>
  )
}
