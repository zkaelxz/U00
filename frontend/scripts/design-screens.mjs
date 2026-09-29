// Captures dark-theme screenshots of the main screens at desktop (1280x800)
// and phone (390x844) sizes, for docs/design/screens/{before,after}/.
// Needs the seeded API and a preview server running (see playwright.config.ts):
//   python e2e/serve_seeded_api.py 8611 &
//   npm run build && BAIHE_API_URL=http://127.0.0.1:8611 npx vite preview --port 4174 &
//   node scripts/design-screens.mjs ../docs/design/screens/after settings,library
// Env: SCREENS_BASE_URL (default http://127.0.0.1:4174/), PLAYWRIGHT_CHROMIUM_PATH.
import { chromium } from '@playwright/test'
const out = process.argv[2]
const only = process.argv[3]
const pages = [
  ['library', '#/'],
  ['drama-detail', '#/', async (p) => { const b = p.getByRole('button', { name: /Details/ }).first(); if (await b.count()) await b.click(); else await p.getByText('Grandmaster of Demonic Cultivation').first().click() }],
  ['ws-source', '#/drama/1/source'],
  ['ws-translate', '#/drama/1/translate'],
  ['ws-review', '#/drama/1/review'],
  ['ws-dub', '#/drama/1/dub'],
  ['ws-export', '#/drama/1/export'],
  ['translate', '#/translate'],
  ['sources', '#/sources'],
  ['reader', '#/read/1'],
  ['settings', '#/settings'],
  ['diagnostics', '#/diagnostics'],
]
const base = process.env.SCREENS_BASE_URL ?? 'http://127.0.0.1:4174/'
const exe = process.env.PLAYWRIGHT_CHROMIUM_PATH
const b = await chromium.launch(exe ? { executablePath: exe } : {})
for (const [vp, size, extra] of [['desktop', { width: 1280, height: 800 }, {}], ['phone', { width: 390, height: 844 }, { isMobile: true, hasTouch: true, deviceScaleFactor: 1 }]]) {
  const ctx = await b.newContext({ viewport: size, colorScheme: 'dark', ...extra })
  const p = await ctx.newPage()
  for (const [name, hash, act] of pages) {
    if (only && !only.split(',').includes(name)) continue
    await p.goto(base + hash)
    await p.waitForLoadState('networkidle').catch(() => {})
    await p.waitForTimeout(500)
    if (act) { try { await act(p); await p.waitForTimeout(600) } catch (e) { console.log(name, e.message) } }
    await p.screenshot({ path: `${out}/${name}-${vp}.png` })
  }
  await ctx.close()
}
await b.close()
