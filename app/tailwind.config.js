/** Colours are CSS variables (src/index.css) so light/dark share one set of classes. */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: 'rgb(var(--bg) / <alpha-value>)', surface: 'rgb(var(--surface) / <alpha-value>)',
        line: 'rgb(var(--line) / <alpha-value>)', ink: 'rgb(var(--ink) / <alpha-value>)',
        muted: 'rgb(var(--muted) / <alpha-value>)', brand: 'rgb(var(--brand) / <alpha-value>)',
        ok: 'rgb(var(--ok) / <alpha-value>)', warn: 'rgb(var(--warn) / <alpha-value>)',
        crit: 'rgb(var(--crit) / <alpha-value>)',
      },
    },
  },
  plugins: [],
}
