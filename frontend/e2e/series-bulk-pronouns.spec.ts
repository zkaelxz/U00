import { expect, test, type Page, type Route } from '@playwright/test'

// Translate > Characters > Series cast > Bulk pronouns (parity X16): tick
// people, pick pronouns, and the page edits them one at a time through the
// per-person route. The drama read hits the real seeded API (reported as
// series 7); every series-people route is mocked, so nothing is written.

test.use({ viewport: { width: 1280, height: 800 } })

const SHOTS = process.env.SHOT_DIR

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

async function openSection(page: Page, title: string) {
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open')) === null) await summary.click()
}

async function inSeries(page: Page) {
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
}

type Person = { id: number; character_name: string; aliases: string; notes: string; pronouns: string }

// In-memory series 7. Edits to an id in `fail` get a 409. The first edit
// waits for `release()` so the progress line can be seen.
async function mockSeries(page: Page, initial: Person[], fail: number[] = []) {
  const people = initial.map((p) => ({ ...p }))
  const writes: { url: string; body: Record<string, string> }[] = []
  let release = () => {}
  const gate = new Promise<void>((r) => (release = r))
  const sorted = () => [...people].sort((a, b) => (a.character_name < b.character_name ? -1 : 1))
  await page.route('**/api/characters/series/7/characters', (route: Route) => route.fulfill({ json: sorted() }))
  await page.route(/\/api\/characters\/series\/7\/characters\/\d+$/, async (route: Route) => {
    const req = route.request()
    const id = Number(new URL(req.url()).pathname.split('/').pop())
    const body = JSON.parse(req.postData() ?? '{}')
    writes.push({ url: new URL(req.url()).pathname, body })
    if (writes.length === 1) await gate
    if (fail.includes(id)) {
      return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Someone else is editing this person.' } } })
    }
    const p = people.find((x) => x.id === id)
    if (!p) return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No such person.' } } })
    Object.assign(p, body)
    return route.fulfill({ json: p })
  })
  return { writes, release: () => release() }
}

// Listed by name: Jiang Cheng, Lan Zhan, Wei Ying.
const CAST: Person[] = [
  { id: 11, character_name: 'Wei Ying', aliases: '', notes: '', pronouns: 'he/him' },
  { id: 12, character_name: 'Lan Zhan', aliases: '', notes: '', pronouns: 'female' },
  { id: 13, character_name: 'Jiang Cheng', aliases: '', notes: '', pronouns: '' },
]

async function openBulk(page: Page) {
  await page.goto('/#/drama/1/translate')
  await openSection(page, 'Characters')
  await openSection(page, 'Series cast')
  await openSection(page, 'Bulk pronouns')
}

test.describe('Series cast bulk pronouns', () => {
  test('sets a preset for two people one at a time and lists the failure', async ({ page }) => {
    await inSeries(page)
    const { writes, release } = await mockSeries(page, CAST, [13])
    await openBulk(page)
    const cast = page.getByTestId('series-cast')
    const run = page.getByRole('button', { name: /^Set pronouns for/ })

    // Nothing ticked: disabled, with the reason in view.
    await expect(run).toBeDisabled()
    await expect(page.getByText('Still needed: tick at least one person in the list.')).toBeVisible()

    await cast.getByRole('checkbox', { name: 'Select Jiang Cheng' }).check()
    await cast.getByRole('checkbox', { name: 'Select Lan Zhan' }).check()
    await expect(run).toHaveText('Set pronouns for 2 selected')

    // Custom with nothing typed is refused.
    const pick = page.getByLabel('Set pronouns to', { exact: true })
    // Nothing is chosen yet: the run waits for a choice.
    await expect(page.getByText('Still needed: choose the pronouns to set.')).toBeVisible()
    await pick.selectOption({ label: 'Custom…' })
    await expect(run).toBeDisabled()
    await expect(page.getByText('Still needed: type the custom pronouns.')).toBeVisible()

    await pick.selectOption('they/them')
    await expect(run).toBeEnabled()
    await run.click()
    await expect(page.getByRole('status').filter({ hasText: 'Updating 1 of 2…' })).toBeVisible()
    await expect.poll(() => writes.length).toBe(1) // sequential: the second waits for the first
    release()

    await expect(page.getByRole('status').filter({ hasText: 'Updated 1 person. 1 person could not be updated.' })).toBeVisible()
    expect(writes).toEqual([
      { url: '/api/characters/series/7/characters/13', body: { pronouns: 'they/them' } },
      { url: '/api/characters/series/7/characters/12', body: { pronouns: 'they/them' } },
    ])
    const failures = page.locator('.series-bulk-failures')
    await expect(failures.locator('li')).toHaveCount(1)
    await expect(failures).toContainText('Jiang Cheng')
    await expect(failures).toContainText('Someone else is editing this person.')

    // The success is in the list; the failure stays ticked for a retry.
    await expect(cast.locator('li').filter({ hasText: 'Lan Zhan' })).toContainText('they/them')
    await expect(cast.getByRole('checkbox', { name: 'Select Jiang Cheng' })).toBeChecked()
    await expect(cast.getByRole('checkbox', { name: 'Select Lan Zhan' })).not.toBeChecked()
    await expect(run).toHaveText('Set pronouns for 1 selected')
    await shot(page, 'series-bulk-pronouns-desktop')
  })

  test('select all skips people who already match; fits a phone width', async ({ page }) => {
    await inSeries(page)
    const { writes, release } = await mockSeries(page, CAST)
    release()
    await page.setViewportSize({ width: 390, height: 844 })
    await openBulk(page)
    await page.locator('.series-bulk').getByRole('button', { name: 'Select all', exact: true }).click()
    const run = page.getByRole('button', { name: 'Set pronouns for 3 selected' })
    await page.getByLabel('Set pronouns to', { exact: true }).selectOption('he/him')
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
    const box = await page.getByTestId('series-cast').locator('.series-cast-pick').first().boundingBox()
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    await shot(page, 'series-bulk-pronouns-phone')
    await run.click()
    await expect(page.getByRole('status').filter({ hasText: 'Updated 2 people. 1 person already had he/him.' })).toBeVisible()
    expect(writes.map((w) => w.url)).toEqual(['/api/characters/series/7/characters/13', '/api/characters/series/7/characters/12'])
    expect(writes.every((w) => JSON.stringify(w.body) === '{"pronouns":"he/him"}')).toBe(true)
    await expect(page.locator('.series-bulk-failures')).toHaveCount(0)
  })
})
