/// <reference types="vitest" />
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'
import { VitePWA } from 'vite-plugin-pwa'

// Dev: `npm run dev` proxies /api and /ws to the edge service (make sim) so the app works same-origin.
const EDGE = process.env.VITE_EDGE ?? 'http://localhost:8080'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      strategies: 'injectManifest', srcDir: 'src', filename: 'sw.ts', registerType: 'autoUpdate',
      injectManifest: { globPatterns: ['**/*.{js,css,html,svg,png}'] },
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'SenseBand', short_name: 'SenseBand', description: 'Health monitoring and recovery tracking',
        theme_color: '#0f766e', background_color: '#f6f8f8', display: 'standalone', start_url: './',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
        ],
      },
    }),
  ],
  server: { proxy: { '/api': EDGE, '/ws': { target: EDGE, ws: true } } },
  test: { environment: 'jsdom', globals: true, setupFiles: ['./src/test-setup.ts'], css: false },
})
