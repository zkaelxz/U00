import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The React app calls the API at the relative path /api, and the dev and
// preview servers forward that to FastAPI. The browser only ever sees
// one origin, so development needs no CORS and the built app works
// unchanged behind anything that serves it next to the API.
const apiTarget = process.env.BAIHE_API_URL ?? 'http://127.0.0.1:8600'
const proxy = { '/api': { target: apiTarget, changeOrigin: false } }

// The Discover Catalogue tab is a compile-time constant that the minifier removes along with the tab, so only
// a build with the constant on can run its e2e tests: BAIHE_E2E_CATALOGUE=1 (playwright.config.ts) makes one.
const catalogueFile = 'src/pages/discover/discoverFormat.ts'
const catalogueOn = {
  name: 'e2e-catalogue-tab',
  enforce: 'pre' as const,
  transform(code: string, id: string) {
    if (!id.endsWith(catalogueFile)) return null
    const on = code.replace('CATALOGUE_TAB_ENABLED = false', 'CATALOGUE_TAB_ENABLED = true')
    if (on === code) throw new Error(`${catalogueFile} no longer declares CATALOGUE_TAB_ENABLED = false`)
    return on
  },
}

export default defineConfig({
  plugins: [react(), ...(process.env.BAIHE_E2E_CATALOGUE === '1' ? [catalogueOn] : [])],
  // Loopback only, same default as the API itself.
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy },
  test: { include: ['src/**/*.test.{ts,tsx}'], environment: 'node' },
})
