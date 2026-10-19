import { defineConfig, devices } from '@playwright/test'

// End-to-end: a real browser -> the built React app (vite preview) ->
// its /api proxy -> the real FastAPI app on a seeded throwaway library.
// Ports differ from the everyday dev defaults so this can run while a
// normal dev stack is up; E2E_API_PORT / E2E_WEB_PORT override them so two
// runs can share a machine.
const API_PORT = Number(process.env.E2E_API_PORT ?? 8611)
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 4174)
// A second build with the Discover Catalogue tab on, for the specs tagged @catalogue.
const CATALOGUE_PORT = Number(process.env.E2E_CATALOGUE_PORT ?? 4175)
const python = process.env.PYTHON ?? 'python'

// A browser with no saved choice starts with the labelled rail on a wide screen. The specs were written for the floating pill, so
// they start from a saved "float" choice; first-launch.spec.ts clears it to see what a newcomer sees.
const floatingNav = (port: number) => ({
  cookies: [],
  origins: [{ origin: `http://127.0.0.1:${port}`, localStorage: [{ name: 'baihe.navDock', value: 'float' }] }],
})

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  // One worker: the specs share one seeded throwaway library (library-page.spec.ts
  // creates and deletes a drama while library.spec.ts asserts a count of 3).
  workers: 1,
  // A committed test.only would silently run one test and pass.
  forbidOnly: !!process.env.CI,
  // The HTML report and traces are uploaded by the e2e job when it fails; the JSON file feeds tools/check_e2e_skips.py.
  reporter: process.env.CI
    ? [['list'], ['html', { open: 'never' }], ['json', { outputFile: 'test-results/results.json' }]]
    : 'list',
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Lets a machine with a preinstalled Chromium (e.g. PLAYWRIGHT_CHROMIUM_PATH=
    // /opt/pw-browsers/chromium) skip `playwright install`.
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
      : {},
  },
  projects: [
    // Every other spec runs at the default desktop viewport.
    { name: 'desktop', testIgnore: /mobile\.spec\.ts/, grepInvert: /@catalogue/, use: { storageState: floatingNav(WEB_PORT) } },
    // Only the @catalogue tests, against the build that has the Catalogue tab on.
    {
      name: 'catalogue',
      testMatch: /discover\.spec\.ts/,
      grep: /@catalogue/,
      use: { baseURL: `http://127.0.0.1:${CATALOGUE_PORT}`, storageState: floatingNav(CATALOGUE_PORT) },
    },
    // Phone checks (touch targets, no sideways scroll) run only here.
    {
      name: 'phone',
      testMatch: /mobile\.spec\.ts/,
      use: {
        browserName: 'chromium',
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 3,
        hasTouch: true,
        isMobile: true,
        userAgent: devices['iPhone 13'].userAgent,
      },
    },
  ],
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
    {
      command: `npx vite build --outDir dist-catalogue --emptyOutDir && npx vite preview --outDir dist-catalogue --port ${CATALOGUE_PORT}`,
      url: `http://127.0.0.1:${CATALOGUE_PORT}`,
      env: { BAIHE_API_URL: `http://127.0.0.1:${API_PORT}`, BAIHE_E2E_CATALOGUE: '1' },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
