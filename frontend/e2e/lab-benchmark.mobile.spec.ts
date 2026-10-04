import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { installHitArea } from './hitArea'

// .btn-sm keeps a 44px hit area but is 32px tall: measure the hit area, not the box.
test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

// Phone project (390x844, touch): the Benchmark Lab with a golden set and two
// finished runs of the fake engine, seeded through the real API
// (nothing is spent). Checks run cards, the Arena stacked one run per row,
// 44px targets and no sideways scroll. BENCH_SHOTS_DIR=<dir> saves a screenshot.
// Named lab-* on purpose: a run leaves a finished "benchmark_lab" job on the
// shared e2e server (there is no route to clear it), and diagnostics.spec.ts
// expects an empty job list, so these specs must sort after it.

const SHOTS = process.env.BENCH_SHOTS_DIR
const LOCAL = { 'X-Baihe-Local': '1' }

async function noSideways(page: Page) {
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)
}

async function smallTargets(page: Page) {
  return page.locator('.bench-page button:not(.link):not(.field-help-btn):not(.toggle), .bench-page summary, .bench-page select, .bench-page input:not([type=checkbox]), .bench-page a.btn').evaluateAll((els) =>
    els.filter((e) => (e as HTMLElement).offsetParent !== null)
      .map((e) => ({ h: window.hitHeight(e), text: (e.textContent || e.getAttribute('aria-label') || '').trim().slice(0, 30) }))
      .filter(({ h }) => h < 44))
}

async function waitForJob(request: APIRequestContext) {
  await expect.poll(async () => (await (await request.get('/api/jobs/benchmark_lab')).json()).status, { timeout: 15_000 }).toBe('done')
}

async function seedRun(request: APIRequestContext, setName: string, label: string) {
  const resp = await request.post('/api/benchmark/runs', {
    headers: LOCAL,
    data: { stage: 'translation', configs: [{ engine: 'fake' }], set_name: setName, label, prompt_version: label, confirm: true },
  })
  expect(resp.status()).toBe(200)
  await waitForJob(request)
}

test('Benchmark Lab on a phone: run cards, stacked Arena, 44px targets, no sideways scroll', async ({ page, request }) => {
  const setName = `phone-${Date.now()}`
  const imp = await request.post('/api/benchmark/import', {
    headers: LOCAL,
    data: {
      set_name: setName, format: 'jsonl', tier: 'application', source_language: 'zh',
      text: '{"source": "今天天气很好，我们去公园散步吧。", "reference": "The weather is lovely today; let us walk in the park."}\n{"source": "谢谢", "reference": "Thank you"}',
    },
  })
  expect((await imp.json()).added).toBe(2)
  await seedRun(request, setName, 'phone A')
  await seedRun(request, setName, 'phone B')

  await page.goto('/#/benchmark')
  await expect(page.getByRole('heading', { name: 'Benchmark Lab' })).toBeVisible()
  const runs = page.getByRole('region', { name: 'Recent runs' })
  // Phones get cards, not the table.
  await expect(runs.locator('table')).toHaveCount(0)
  await expect(runs.getByRole('list', { name: 'Runs' }).locator(':scope > li', { hasText: 'phone A' })).toContainText('Done')
  await noSideways(page)

  // Golden sets as cards too; open the set's cases.
  const sets = page.getByRole('region', { name: 'Golden sets' })
  await sets.getByRole('button', { name: `Show cases in ${setName} (Application)` }).click()
  await expect(sets.getByRole('region', { name: `Cases in ${setName}` }).locator('li')).toHaveCount(2)
  await noSideways(page)

  // Tick both runs by tapping their row labels, compare.
  await runs.getByRole('checkbox', { name: /phone A$/ }).check()
  await runs.getByRole('checkbox', { name: /phone B$/ }).check()
  await runs.getByRole('button', { name: 'Compare in Arena (2)' }).click()
  const arena = page.getByRole('region', { name: 'Model Arena' })
  await expect(arena.getByRole('list', { name: 'Cases' }).locator(':scope > li')).toHaveCount(2)

  // Stacked: the two runs' outputs for a case sit one above the other.
  const cells = arena.getByRole('list', { name: 'Cases' }).locator(':scope > li').first().locator('.bench-arena-cell')
  const [a, b] = await cells.evaluateAll((els) => els.slice(0, 2).map((e) => {
    const r = e.getBoundingClientRect()
    return { x: r.x, y: r.y, height: r.height }
  }))
  expect(Math.abs(a.x - b.x)).toBeLessThan(2)
  expect(b.y).toBeGreaterThanOrEqual(a.y + a.height - 1)

  await noSideways(page)
  expect(await smallTargets(page)).toEqual([])

  if (SHOTS) {
    await page.evaluate(() => window.scrollTo(0, 0))
    await page.screenshot({ path: `${SHOTS}/benchmark-phone.png`, fullPage: true })
    await arena.scrollIntoViewIfNeeded()
    await page.screenshot({ path: `${SHOTS}/benchmark-phone-arena.png` })
  }
})
