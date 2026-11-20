import '@testing-library/jest-dom/vitest'

// jsdom has no layout engine: Recharts' ResponsiveContainer needs ResizeObserver, and the ECG canvas needs a context.
class RO { observe() { /* noop */ } unobserve() { /* noop */ } disconnect() { /* noop */ } }
;(globalThis as unknown as { ResizeObserver: typeof RO }).ResizeObserver = RO
HTMLCanvasElement.prototype.getContext = (() => null) as unknown as HTMLCanvasElement['getContext']
