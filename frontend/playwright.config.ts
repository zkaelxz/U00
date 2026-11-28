import path from 'node:path'
import { fileURLToPath } from 'node:url'

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
// The API server and every spec's seeding script load e2e/python-site/sitecustomize.py, which gives sqlite a longer wait
// for another process's write lock. Workers load this file too, so the folder is added once.
const pythonSite = fileURLToPath(new URL('./e2e/python-site', import.meta.url))
const pythonPath = (process.env.PYTHONPATH ?? '').split(path.delimiter).filter(Boolean)
if (!pythonPath.includes(pythonSite)) process.env.PYTHONPATH = [pythonSite, ...pythonPath].join(path.delimiter)
// CI builds both bundles in a step of their own, so a slow build on a busy runner can't run out a server's start-up timeout.
const prebuilt = !!process.env.E2E_PREBUILT

// A browser with no saved choice starts with the labelled rail on a wide screen. The specs were written for the floating pill, so
// they start from a saved "float" choice; first-launch.spec.ts clears it to see what a newcomer sees.
// They also start from the Full title view and the Advanced menu, which the specs were written against; simple-title.spec.ts and view-mode.spec.ts clear them to see the defaults.
const floatingNav = (port: number) => ({
  cookies: [],
  origins: [{
    origin: `http://127.0.0.1:${port}`,
    localStorage: [
      { name: 'baihe.navDock', value: 'float' },
      { name: 'baihe.pref.title.view', value: '"full"' },
      { name: 'baihe.pref.title.view.comic', value: '"full"' },
      { name: 'baihe.pref.ui.mode', value: '"advanced"' },
      // These blocks start folded for a newcomer; the specs were written against them open, and fold-defaults.spec.ts clears them.
      ...['diagnostics.packages', 'diagnostics.models', 'diagnostics.omnivoice', 'diagnostics.qwen3-tts', 'diagnostics.chatterbox',
        'diagnostics.modelgroup.whisper', 'diagnostics.modelgroup.speech', 'diagnostics.modelgroup.translation', 'diagnostics.modelgroup.other',
        'benchmark.addCorrections', 'benchmark.buildSet', 'benchmark.importSet', 'benchmark.addCase', 'benchmark.handRuns']
        .map((k) => ({ name: `baihe.section.${k}`, value: '1' })),
    ],
  }],
})

// CI runs the phone project in jobs of its own and every other project in the rest (the e2e job in
// .github/workflows/tests.yml): Playwright shards by test count, and a phone test takes about twice as long.
// "desktop" means everything but phone, so a new project can never fall out of CI.
function projectGroup<T extends { name: string }>(name: string | undefined, all: T[]): T[] {
  if (name === 'phone') return all.filter(p => p.name === 'phone')
  if (name === 'desktop') return all.filter(p => p.name !== 'phone')
  if (name) throw new Error(`E2E_PROJECT_GROUP must be phone or desktop, not ${name}`)
  return all
}

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
  projects: projectGroup(process.env.E2E_PROJECT_GROUP, [
    // Every other spec runs at the default desktop viewport.
    { name: 'desktop', testIgnore: /mobile\.spec\.ts/, grepInvert: /@catalogue/, use: { storageState: floatingNav(WEB_PORT) } },
    // Only the @catalogue tests, against the build that has the Catalogue tab on.
    {
      name: 'catalogue',
      testMatch: /discover(-pasted)?\.spec\.ts/,
      grep: /@catalogue/,
      use: { baseURL: `http://127.0.0.1:${CATALOGUE_PORT}`, storageState: floatingNav(CATALOGUE_PORT) },
    },
    // Phone checks (touch targets, no sideways scroll) run only here.
    {
      name: 'phone',
      testMatch: /mobile\.spec\.ts/,
      use: {
        browserName: 'chromium' as const,
        storageState: floatingNav(WEB_PORT),
        viewport: { width: 390, height: 844 },
        deviceScaleFactor: 3,
        hasTouch: true,
        isMobile: true,
        userAgent: devices['iPhone 13'].userAgent,
      },
    },
  ]),
  webServer: [
    {
      command: `${python} e2e/serve_seeded_api.py ${API_PORT}`,
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `${prebuilt ? '' : 'npm run build && '}npx vite preview --port ${WEB_PORT}`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { BAIHE_API_URL: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `${prebuilt ? '' : 'npx vite build --outDir dist-catalogue --emptyOutDir && '}npx vite preview --outDir dist-catalogue --port ${CATALOGUE_PORT}`,
      url: `http://127.0.0.1:${CATALOGUE_PORT}`,
      env: { BAIHE_API_URL: `http://127.0.0.1:${API_PORT}`, BAIHE_E2E_CATALOGUE: '1' },
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
})
