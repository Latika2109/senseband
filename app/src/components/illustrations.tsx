// Simple, high-contrast wear-guide pictures (inline SVG, no external images, work offline).
const S = { fill: 'none', stroke: 'currentColor', strokeWidth: 3, strokeLinecap: 'round', strokeLinejoin: 'round' } as const

export function WaistArt() {
  return (<svg viewBox="0 0 120 120" className="h-24 w-24 text-brand" role="img" aria-label="Band on waist">
    <path {...S} d="M40 12c-8 8-10 26-10 40 0 14 4 30 6 56M80 12c8 8 10 26 10 40 0 14-4 30-6 56" />
    <rect x="30" y="58" width="60" height="16" rx="6" fill="currentColor" opacity=".18" stroke="currentColor" strokeWidth="3" />
    <rect x="50" y="60" width="20" height="12" rx="3" fill="currentColor" />
  </svg>)
}
export function ChestArt() {
  return (<svg viewBox="0 0 120 120" className="h-24 w-24 text-brand" role="img" aria-label="Electrodes on chest">
    <path {...S} d="M30 20c10 6 20 8 30 8s20-2 30-8v70c-10 8-20 12-30 12s-20-4-30-12z" />
    <circle cx="42" cy="38" r="6" fill="currentColor" /><circle cx="78" cy="38" r="6" fill="currentColor" />
    <circle cx="48" cy="82" r="6" fill="currentColor" />
    <text x="30" y="30" fontSize="9" fill="currentColor">RA</text><text x="84" y="30" fontSize="9" fill="currentColor">LA</text>
    <text x="55" y="96" fontSize="9" fill="currentColor">RL</text>
  </svg>)
}
export function ClipArt() {
  return (<svg viewBox="0 0 120 120" className="h-24 w-24 text-brand" role="img" aria-label="Clip on finger">
    <rect x="46" y="14" width="28" height="84" rx="14" {...S} />
    <rect x="38" y="46" width="44" height="26" rx="8" fill="currentColor" opacity=".25" stroke="currentColor" strokeWidth="3" />
    <circle cx="60" cy="59" r="5" fill="currentColor" />
    <path {...S} d="M82 59h20c6 0 8 4 8 10" />
  </svg>)
}
export function StrapArt() {
  return (<svg viewBox="0 0 120 120" className="h-24 w-24 text-brand" role="img" aria-label="Chest strap">
    <rect x="14" y="44" width="92" height="22" rx="10" fill="currentColor" opacity=".2" stroke="currentColor" strokeWidth="3" />
    <rect x="46" y="42" width="28" height="26" rx="6" fill="currentColor" />
    <path {...S} d="M30 80l12-6 12 12 12-12 12 6" />
  </svg>)
}
export function BatteryArt() {
  return (<svg viewBox="0 0 120 120" className="h-24 w-24 text-warn" role="img" aria-label="Battery only">
    <rect x="22" y="40" width="68" height="40" rx="8" {...S} /><rect x="92" y="53" width="8" height="14" rx="2" fill="currentColor" />
    <rect x="30" y="48" width="36" height="24" rx="3" fill="currentColor" />
    <path {...S} d="M24 14l72 92" />
  </svg>)
}
