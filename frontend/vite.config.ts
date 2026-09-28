import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// The React app calls the API at the relative path /api, and the dev and
// preview servers forward that to FastAPI. The browser only ever sees
// one origin, so development needs no CORS and the built app works
// unchanged behind anything that serves it next to the API.
const apiTarget = process.env.BAIHE_API_URL ?? 'http://127.0.0.1:8600'
const proxy = { '/api': { target: apiTarget, changeOrigin: false } }

export default defineConfig({
  plugins: [react()],
  // Loopback only, same default as the API itself.
  server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
})
