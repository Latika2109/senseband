import { useEffect, useRef } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

/** Scrolling ECG on a canvas (cheap enough for a Pi-served phone page at 5 Hz updates). */
export function EcgCanvas({ samples, label }: { samples: number[]; label: string }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const c = ref.current
    const ctx = c?.getContext('2d')
    if (!c || !ctx) return
    const w = (c.width = c.clientWidth * (window.devicePixelRatio || 1))
    const h = (c.height = c.clientHeight * (window.devicePixelRatio || 1))
    ctx.clearRect(0, 0, w, h)
    const cs = getComputedStyle(c)
    ctx.strokeStyle = `rgb(${cs.getPropertyValue('--brand')})`
    ctx.lineWidth = 2 * (window.devicePixelRatio || 1)
    if (samples.length < 2) return
    const lo = Math.min(...samples), hi = Math.max(...samples)
    const span = Math.max(hi - lo, 0.5), pad = h * 0.1
    ctx.beginPath()
    samples.forEach((v, i) => {
      const x = (i / (samples.length - 1)) * w
      const y = h - pad - ((v - lo) / span) * (h - 2 * pad)
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y)
    })
    ctx.stroke()
  }, [samples])
  return <canvas ref={ref} role="img" aria-label={label} className="h-32 w-full rounded-xl bg-bg" />
}

export function HrChart({ data, label }: { data: { ts: number; hr: number | null; expected: number }[]; label: string }) {
  const pts = data.map((d) => ({ t: new Date(d.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }), hr: d.hr, expected: d.expected }))
  return (
    <div role="img" aria-label={label} className="h-48 w-full">
      <ResponsiveContainer>
        <LineChart data={pts} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
          <CartesianGrid stroke="rgb(var(--line))" strokeDasharray="3 3" />
          <XAxis dataKey="t" tick={{ fontSize: 11, fill: 'rgb(var(--muted))' }} minTickGap={40} />
          <YAxis domain={['dataMin - 10', 'dataMax + 10']} width={36} tick={{ fontSize: 11, fill: 'rgb(var(--muted))' }} />
          <Tooltip />
          <Line dataKey="expected" stroke="rgb(var(--muted))" strokeDasharray="5 4" dot={false} strokeWidth={2} isAnimationActive={false} />
          <Line dataKey="hr" stroke="rgb(var(--brand))" dot={false} strokeWidth={2.5} connectNulls={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
