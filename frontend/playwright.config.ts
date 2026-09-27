import { defineConfig } from '@playwright/test'

// End-to-end: a real browser -> the built React app (vite preview) ->
// its /api proxy -> the real FastAPI app on a seeded throwaway library.
// Ports differ from the everyday dev defaults so this can run while a
// normal dev stack is up.
const API_PORT = 8611
const WEB_PORT = 4174
const python = process.env.PYTHON ?? 'python'

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    // Lets a machine with a preinstalled Chromium (e.g. PLAYWRIGHT_CHROMIUM_PATH=
    // /opt/pw-browsers/chromium) skip `playwright install`.
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
      : {},
  },
  webServer: [
    {
      command: `${python} e2e/serve_seeded_api.py ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `npm run build && npx vite preview --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { BAIHE_API_URL: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
