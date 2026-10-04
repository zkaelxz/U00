import { expect, test, type Page, type Route } from '@playwright/test'

// Translate > Characters > Series cast: add a person and edit name,
// pronouns and aliases inline (parity X15-X17). The drama read hits the
// real seeded API (reported as series 7); every series-people route is
// mocked and always fulfilled, so the shared library is never written.

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

// In-memory series 7: GET lists, POST adds (409 on a taken name), POST
// .../{id} edits. Records every write body.
async function mockSeries(page: Page, initial: Person[]) {
  const people = initial.map((p) => ({ ...p })) // edits must not leak into CAST
  const writes: { url: string; body: Record<string, string> }[] = []
  let nextId = 100
  const sorted = () => [...people].sort((a, b) => (a.character_name < b.character_name ? -1 : 1))
  await page.route('**/api/characters/series/7/characters', async (route: Route) => {
    const req = route.request()
    if (req.method() !== 'POST') return route.fulfill({ json: sorted() })
    const body = JSON.parse(req.postData() ?? '{}')
    writes.push({ url: new URL(req.url()).pathname, body })
    if (people.some((p) => p.character_name === body.character_name)) {
      return route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'This series already has someone with that name.' } } })
    }
    const p = { id: nextId++, character_name: body.character_name, aliases: body.aliases ?? '', notes: body.notes ?? '', pronouns: body.pronouns ?? '' }
    people.push(p)
    return route.fulfill({ json: p })
  })
  await page.route(/\/api\/characters\/series\/7\/characters\/\d+$/, async (route: Route) => {
    const req = route.request()
    const id = Number(new URL(req.url()).pathname.split('/').pop())
    const body = JSON.parse(req.postData() ?? '{}')
    writes.push({ url: new URL(req.url()).pathname, body })
    const p = people.find((x) => x.id === id)
    if (!p) return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No such person.' } } })
    Object.assign(p, body)
    return route.fulfill({ json: p })
  })
  return writes
}

const CAST: Person[] = [
  { id: 11, character_name: 'Wei Ying', aliases: 'Wei Wuxian', notes: '', pronouns: 'he/him' },
  { id: 12, character_name: 'Lan Zhan', aliases: '', notes: '', pronouns: 'female' },
]

test.describe('Series cast editing', () => {
  test('edits a person inline by id and adds one', async ({ page }) => {
    await inSeries(page)
    const writes = await mockSeries(page, CAST)
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Characters')
    await openSection(page, 'Series cast')
    const cast = page.getByTestId('series-cast')
    await expect(cast.locator('li')).toHaveCount(2)

    await cast.getByRole('button', { name: 'Edit Lan Zhan' }).click()
    const edit = page.getByRole('group', { name: 'Edit Lan Zhan' })
    // Legacy "female" shows as she/her and is not a change by itself.
    await expect(edit.getByLabel('Pronouns', { exact: true })).toHaveValue('she/her')
    await expect(edit.getByRole('button', { name: 'Save' })).toBeDisabled()
    await edit.getByLabel('Name', { exact: true }).fill('Lan Wangji')
    await edit.getByLabel('Pronouns', { exact: true }).selectOption({ label: 'Custom…' })
    await edit.getByLabel('Custom pronouns', { exact: true }).fill('xe/xem')
    await edit.getByLabel('Aliases', { exact: true }).fill('Lan Zhan|蓝湛')
    await shot(page, 'series-cast-edit-desktop')
    await edit.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByRole('status').filter({ hasText: 'Saved Lan Wangji.' })).toBeVisible()
    await expect(cast.locator('li').filter({ hasText: 'Lan Wangji' })).toContainText('xe/xem')
    expect(writes[0]).toEqual({
      url: '/api/characters/series/7/characters/12',
      body: { character_name: 'Lan Wangji', pronouns: 'xe/xem', aliases: 'Lan Zhan|蓝湛' },
    })

    const add = page.getByRole('group', { name: 'Add a person to the series' })
    await expect(add.getByRole('button', { name: 'Add person' })).toBeDisabled()
    await add.getByLabel('Name', { exact: true }).fill('Jiang Cheng')
    await add.getByLabel('Pronouns', { exact: true }).selectOption('he/him')
    await add.getByRole('button', { name: 'Add person' }).click()
    await expect(cast.locator('li')).toHaveCount(3)
    await expect(add.getByLabel('Name', { exact: true })).toHaveValue('')
    expect(writes[1]).toEqual({
      url: '/api/characters/series/7/characters',
      body: { character_name: 'Jiang Cheng', pronouns: 'he/him', aliases: '', notes: '' },
    })

    // A taken name: the 409 is shown, nothing is added.
    await add.getByLabel('Name', { exact: true }).fill('Wei Ying')
    await add.getByRole('button', { name: 'Add person' }).click()
    await expect(add.getByRole('alert')).toBeVisible()
    await expect(cast.locator('li')).toHaveCount(3)
    await shot(page, 'series-cast-add-desktop')
  })

  test('an empty series shows the add form', async ({ page }) => {
    await inSeries(page)
    const writes = await mockSeries(page, [])
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Characters')
    await openSection(page, 'Series cast')
    await expect(page.getByText('No one yet.')).toBeVisible()
    const add = page.getByRole('group', { name: 'Add a person to the series' })
    await add.getByLabel('Name', { exact: true }).fill('Mei')
    await add.getByRole('button', { name: 'Add person' }).click()
    await expect(page.getByTestId('series-cast').locator('li')).toHaveCount(1)
    expect(writes).toHaveLength(1)
  })

  test('remote: editing stays, removing is hidden; fits a phone width', async ({ page }) => {
    await page.route('**/api/meta', (route) =>
      route.fulfill({ json: { app: 'baihe', api_version: '1', environment: 'development', local: false } }),
    )
    await inSeries(page)
    await mockSeries(page, CAST)
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Characters')
    await openSection(page, 'Series cast')
    const cast = page.getByTestId('series-cast')
    await expect(cast.getByRole('button', { name: 'Edit Wei Ying' })).toBeVisible()
    await expect(cast.getByRole('button', { name: 'Remove Wei Ying', exact: true })).toHaveCount(0)
    await cast.getByRole('button', { name: 'Edit Wei Ying' }).click()
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow).toBeLessThanOrEqual(0)
    await shot(page, 'series-cast-edit-phone')
  })
})
