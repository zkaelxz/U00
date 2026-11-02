import { defineConfig } from '@playwright/test'

// Release smoke: one real server (the built app plus its API, on an empty throwaway library) and one browser walk through it.
// Separate from playwright.config.ts because it needs the production bundle served by FastAPI, not vite preview with a seeded library.
const PORT = Number(process.env.SMOKE_PORT ?? 8621)
const python = process.env.PYTHON ?? 'python'

export default defineConfig({
  testDir: './smoke',
  timeout: 90_000,
  workers: 1,
  forbidOnly: !!process.env.CI,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    storageState: {
      cookies: [],
      origins: [{ origin: `http://127.0.0.1:${PORT}`, localStorage: [{ name: 'baihe.navDock', value: 'float' }] }],
    },
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH } : {},
  },
  webServer: {
    command: `${python} e2e/serve_smoke_api.py ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
})
