import { expect, test, type Page } from '@playwright/test'

// Seed data: e2e/serve_seeded_api.py.
// Creates then deletes its own drama so the seeded three are unchanged afterwards.

test('stats line, Continue shelf and global line search', async ({ page }) => {
  // Other specs write lines for Signal into the shared library; with none it is the Open tile whatever ran before.
  await page.route((u) => u.pathname === '/api/library/dramas', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const res = await route.fetch()
    const body = await res.json()
    const items = body.items.map((d: { title_en: string }) => (d.title_en === 'Signal' ? { ...d, line_count: 0, translated_count: 0 } : d))
    await route.fulfill({ response: res, json: { ...body, items } })
  })
  await page.goto('/')
  await expect(page.getByTestId('stats')).toContainText('3 titles')
  const shelf = page.getByRole('region', { name: 'Continue' })
  // One entry per seeded drama (reading or workspace activity).
  await expect(shelf.getByRole('listitem')).toHaveCount(3)
  const open = shelf.getByRole('link', { name: 'Open: Signal', exact: true })
  // A title with no lines yet opens on its current stage: no stage in the link (the workspace picks it).
  await expect(open).toHaveAttribute('href', /#\/drama\/\d+$/)
  await expect(open).toHaveText('Open')
  await expect(open).toHaveClass(/btn/)

  await page.getByRole('radio', { name: 'Lines' }).check()
  await page.getByLabel('Search all lines').fill('zzz-no-such-line')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByTestId('search-count')).toHaveText('0 matches')
  // Back to Titles: the same text now filters titles (no match), and
  // Clear filters brings the whole library back.
  await page.getByRole('radio', { name: 'Titles' }).check()
  await expect(page.getByLabel('Search title or summary')).toHaveValue('zzz-no-such-line')
  await expect(page.getByText('No titles match.')).toBeVisible()
  await page.getByRole('button', { name: 'Clear filters' }).click()
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
})

test('create form validates client-side', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Add a title by hand' }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await expect(sheet.getByLabel('English title')).toBeFocused()
  await sheet.getByRole('button', { name: 'Create title' }).click()
  await expect(sheet.getByRole('alert')).toContainText('title')
  // Humanized options; the values stay raw.
  await expect(sheet.getByLabel('Media type').locator('option:checked')).toHaveText('Audio drama')
  await expect(sheet.getByLabel('Source language').locator('option:checked')).toHaveText('Chinese')
})

test('create form puts focus on the invalid field and hides credits that do not fit the type', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Add a title by hand' }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await sheet.getByLabel('Source language').focus()
  await sheet.getByRole('button', { name: 'Create title' }).click()
  const title = sheet.getByLabel('English title')
  await expect(title).toHaveAttribute('aria-invalid', 'true')
  await expect(title).toBeFocused()
  await sheet.getByText('Credits, summary and series').click()
  await expect(sheet.getByLabel('Voice actors', { exact: true })).toBeVisible()
  await sheet.getByLabel('Media type').selectOption('novel')
  await expect(sheet.getByLabel('Voice actors', { exact: true })).toHaveCount(0)
  await expect(sheet.getByLabel('Studio')).toHaveCount(0)
})

test('New title keeps what was typed when the sheet closes; Cancel discards it', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'Add a title by hand' }).click()
  let sheet = page.getByRole('dialog', { name: 'New title' })
  await sheet.getByLabel('English title').fill('Draft title')
  await sheet.getByLabel('Media type').selectOption('novel')
  await page.keyboard.press('Escape')
  await expect(sheet).toBeHidden()

  await page.getByRole('button', { name: 'Add a title by hand' }).click()
  sheet = page.getByRole('dialog', { name: 'New title' })
  await expect(sheet.getByLabel('English title')).toHaveValue('Draft title')
  await expect(sheet.getByLabel('Media type')).toHaveValue('novel')
  await sheet.getByRole('button', { name: 'Close' }).click()
  await page.getByRole('button', { name: 'Add a title by hand' }).click()
  await expect(sheet.getByLabel('English title')).toHaveValue('Draft title')

  await sheet.getByRole('button', { name: 'Discard draft' }).click()
  await expect(sheet).toBeHidden()
  await page.getByRole('button', { name: 'Add a title by hand' }).click()
  await expect(sheet.getByLabel('English title')).toHaveValue('')
  await expect(sheet.getByLabel('Media type')).toHaveValue('audio_drama')
  // Nothing was created.
  await expect(page.getByTestId('drama-count')).toHaveText('3 titles')
})

test('create (Enter submits) then delete with typed confirmation', async ({ page }) => {
  try {
    await page.goto('/')
    await page.getByRole('button', { name: 'Add a title by hand' }).click()
    await page.getByLabel('English title').fill('E2E Temp Title')
    await page.getByLabel('English title').press('Enter')
    // Creating goes straight to the new drama's workspace; its details sheet is one click away in the Library.
    await expect(page).toHaveURL(/#\/drama\/\d+$/)
    await expect(page.getByTestId('drama-title')).toHaveText('E2E Temp Title')
    await page.getByRole('link', { name: 'Back to Library' }).click()
    await page.getByRole('button', { name: 'Details: E2E Temp Title' }).click()
    const detail = page.getByRole('dialog', { name: 'E2E Temp Title' })
    await expect(detail).toBeVisible()

    await detail.getByRole('button', { name: 'Delete title…' }).click()
    const confirm = detail.getByRole('button', { name: 'Delete permanently' })
    await expect(confirm).toBeDisabled()
    await detail.getByLabel('Type DELETE to confirm').fill('DELETE')
    // Held so a second click lands while the first delete is in flight: it must not send another.
    let deletes = 0
    let release!: () => void
    const gate = new Promise<void>((r) => { release = r })
    await page.route(/\/api\/dramas\/\d+\?confirm=true/, async (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback()
      deletes += 1
      await gate
      await route.continue()
    })
    await confirm.click()
    const busy = detail.getByRole('button', { name: 'Deleting…' })
    await expect(busy).toBeDisabled()
    await busy.click({ force: true })
    release()
    await expect(page.getByRole('dialog', { name: 'E2E Temp Title' })).toHaveCount(0)
    expect(deletes).toBe(1)
    await expect(page.getByRole('region', { name: 'Titles' }).getByRole('link', { name: 'E2E Temp Title' })).toHaveCount(0)
  } finally {
    // A failure before the typed delete would leave the title behind and break the "3 titles" count in every later spec.
    const list = await (await page.request.get('/api/library/dramas')).json()
    for (const d of list.items.filter((x: { title_en: string }) => x.title_en === 'E2E Temp Title')) {
      await page.request.delete(`/api/dramas/${d.id}?confirm=true&confirm_text=DELETE`)
    }
  }
})

// Holds GET /api/dramas until released, and serves one typical card (short title, original title,
// type, status and language pills) so the loaded height is fixed.
async function holdOneCard(page: import('@playwright/test').Page, title: string) {
  let release!: () => void
  const gate = new Promise<void>((r) => { release = r })
  await page.route((u) => u.pathname === '/api/library/dramas', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    await gate
    const res = await route.fetch()
    const body = await res.json()
    const one = { ...body.items.find((d: { id: number }) => d.id === 1), title_en: title, custom_tags: [] }
    await route.fulfill({ response: res, json: { ...body, items: [one], count: 1 } })
  })
  return release
}

test('the loading grid has the loaded card height, so the list does not jump', async ({ page }) => {
  const release = await holdOneCard(page, 'Signal')
  await page.goto('/#/library')
  const loading = page.getByRole('status').filter({ hasText: 'Loading titles' })
  await expect(loading).toHaveAttribute('aria-busy', 'true')
  const skeleton = (await page.locator('.drama-grid.skeleton .drama-card').first().boundingBox())!.height
  release()
  const card = page.locator('.drama-grid:not(.skeleton) .drama-card').first()
  await expect(card).toBeVisible()
  expect(Math.abs((await card.boundingBox())!.height - skeleton)).toBeLessThanOrEqual(4)
})

test('a 130-character title clamps on its card and keeps the whole title in a title attribute', async ({ page }) => {
  const long = 'Supercalifragilistic'.repeat(7).slice(0, 130)
  const release = await holdOneCard(page, long)
  release()
  await page.goto('/#/library')
  const link = page.locator('.drama-grid .drama-card-title').first()
  await expect(link).toHaveAttribute('title', long)
  const box = (await link.boundingBox())!
  const cardBox = (await page.locator('.drama-grid .drama-card').first().boundingBox())!
  expect(box.x + box.width).toBeLessThanOrEqual(cardBox.x + cardBox.width)
})

test('line search without permission to read lines: a plain note, no error banner', async ({ page }) => {
  await page.route('**/api/library/search?*', (route) =>
    route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } }),
  )
  await page.goto('/')
  await page.getByRole('radio', { name: 'Lines' }).check()
  await page.getByLabel('Search all lines').fill('anything')
  await page.getByRole('button', { name: 'Search', exact: true }).click()
  await expect(page.getByText('Seeing line text needs permission to read lines. Ask the PC owner.')).toBeVisible()
  await expect(page.locator('.line-hits').getByRole('alert')).toHaveCount(0)
})

test('a link in the Add box, Only add it, opens New title with it filled in; titles wrap instead of being cut', async ({ page }) => {
  await page.route('**/api/media-link/probe', (r) => r.fulfill({ status: 503, json: { error: { code: 'dependency_unavailable', message: 'No yt-dlp.' } } }))
  await page.goto('/')
  const box = page.getByRole('region', { name: 'Add' })
  await box.getByRole('textbox', { name: 'Paste a link' }).fill('https://example.com/watch?v=abc')
  await expect(box.getByTestId('add-found')).toHaveText('Found: a link to example.com. Baihe could not read it.')
  await box.getByRole('button', { name: "Only add it, don't start" }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await expect(sheet.getByLabel(/^Link/)).toHaveValue('https://example.com/watch?v=abc')
  await page.keyboard.press('Escape')

  // A long title must wrap in the Continue card, never end in an ellipsis.
  const style = await page.locator('.continue-title').first().evaluate((el) => {
    const s = getComputedStyle(el)
    return { whiteSpace: s.whiteSpace, textOverflow: s.textOverflow }
  })
  expect(style.whiteSpace).not.toBe('nowrap')
  expect(style.textOverflow).not.toBe('ellipsis')
})

// The Continue shelf and the list it reads its counts from, mocked so the tiles stand in known states. Newest first:
// Blessing read to page 12; Grandmaster with lines left; Signal translated (the check state); Harbor Lights exported;
// Quiet Stream also in the check state, past the four a computer shows until More.
const row = (id: number, title_en: string, media_type: string, over: Record<string, unknown>) => ({
  id, title_en, title_zh: null, author: null, studio: null, director: null, voice_actors: null, status: 'aligned',
  source_language: 'zh', media_type, content_mode: null, series_id: null, translation_engine: null, custom_tags: [],
  created_at: null, updated_at: '2026-11-10T12:00:00', ...over,
})
const SHELF_ROWS = [
  row(1, 'Grandmaster of Demonic Cultivation', 'audio_drama', { line_count: 2000, translated_count: 766 }),
  row(2, "Heaven Official's Blessing", 'novel', { line_count: 90, translated_count: 90 }),
  row(3, 'Signal', 'video_drama', { line_count: 40, translated_count: 40, status: 'translated' }),
  row(61, 'Harbor Lights', 'video_drama', { line_count: 12, translated_count: 12, status: 'exported' }),
  row(62, 'Quiet Stream', 'asmr', { line_count: 8, translated_count: 8, status: 'translated' }),
]

async function mockShelf(page: Page, risk: (id: number) => number | null) {
  await page.route('**/api/library/continue', (r) => r.fulfill({ json: { items: [{
    drama_id: 2, title_en: "Heaven Official's Blessing", title_zh: null, percent_complete: 40, last_page: 12,
    last_accessed_at: '2026-11-10T12:00:09', has_cover_art: false,
  }] } }))
  await page.route('**/api/library/recent', (r) => r.fulfill({ json: { items: SHELF_ROWS.filter((d) => d.id !== 2).map((d, n) => ({
    id: d.id, title_en: d.title_en, title_zh: null, status: d.status, media_type: d.media_type, updated_at: `2026-11-10T12:00:0${8 - n}`,
  })) } }))
  await page.route((u) => u.pathname === '/api/library/dramas', (r) =>
    r.request().method() === 'GET' ? r.fulfill({ json: { items: SHELF_ROWS, count: SHELF_ROWS.length } }) : r.fallback())
  const asked: number[] = []
  await page.route('**/api/review/dramas/*/lines?*', (r) => {
    const url = new URL(r.request().url())
    if (url.searchParams.get('only') !== 'risk') return r.fallback()
    const id = Number(url.pathname.split('/')[4])
    asked.push(id)
    const n = risk(id)
    return n === null
      ? r.fulfill({ status: 500, json: { error: { code: 'internal', message: 'Something went wrong.' } } })
      : r.fulfill({ json: { items: [], total: n, page: 1, page_size: 1, risk_count: n } })
  })
  return asked
}

test('Continue tiles name the next step and open it; only a tile waiting on a check asks for its count', async ({ page }) => {
  const asked = await mockShelf(page, (id) => (id === 3 ? 3 : 0))
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  await expect(shelf.getByRole('listitem')).toHaveCount(4)
  const tiles: [string, string, string][] = [
    ['Read from page 12', "Heaven Official's Blessing", '#/read/2'],
    ['Translate 1,234 lines', 'Grandmaster of Demonic Cultivation', '#/drama/1/translate'],
    ['Check 3 lines', 'Signal', '#/drama/3/review'],
    ['Watch', 'Harbor Lights', '#/read/61?watch=1'],
  ]
  for (const [label, title, href] of tiles) {
    const link = shelf.getByRole('link', { name: `${label}: ${title}`, exact: true })
    await expect(link).toHaveText(label)
    await expect(link).toHaveAttribute('href', href)
  }
  // The badge and the reading line stay as they were.
  await expect(shelf).toContainText('Reading · page 12 · 40%')
  expect(asked).toEqual([3])

  // Quiet Stream comes on screen with More: asked then, and nothing needs a look, so it says Export.
  await shelf.getByRole('button', { name: 'More (1)' }).click()
  await expect(shelf.getByRole('link', { name: 'Export: Quiet Stream', exact: true })).toHaveAttribute('href', '#/drama/62/export')
  expect(asked).toEqual([3, 62])
  await shelf.getByRole('button', { name: 'Less' }).click()
  await shelf.getByRole('button', { name: 'More (1)' }).click()
  await expect(shelf.getByRole('link', { name: 'Export: Quiet Stream', exact: true })).toBeVisible()
  expect(asked).toEqual([3, 62])

  await shelf.getByRole('link', { name: 'Check 3 lines: Signal' }).click()
  await expect(page).toHaveURL(/#\/drama\/3\/review$/)
})

test('a failed count request leaves Check the lines, with no error on the page', async ({ page }) => {
  const asked = await mockShelf(page, () => null)
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  const check = shelf.getByRole('link', { name: 'Check the lines: Signal', exact: true })
  await expect(check).toHaveAttribute('href', '#/drama/3/review')
  await expect.poll(() => asked).toEqual([3])
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('cards say Watch for video and Listen for audio, opening the Reader with the player; Read stays for the rest', async ({ page }) => {
  await mockShelf(page, () => 0)
  await page.goto('/')
  const titles = page.getByRole('region', { name: 'Titles' })
  await expect(titles.getByRole('link', { name: 'Watch Signal', exact: true })).toHaveAttribute('href', '#/read/3?watch=1')
  await expect(titles.getByRole('link', { name: 'Listen Grandmaster of Demonic Cultivation', exact: true })).toHaveAttribute('href', '#/read/1?watch=1')
  // Blessing has a saved reading place, so its card says Continue; the list view names it by kind.
  await expect(titles.getByRole('link', { name: "Continue Heaven Official's Blessing", exact: true })).toHaveAttribute('href', '#/read/2')
  await titles.getByRole('radio', { name: 'List' }).check()
  await expect(titles.getByRole('link', { name: 'Watch Signal', exact: true })).toHaveAttribute('href', '#/read/3?watch=1')
  await expect(titles.getByRole('link', { name: "Read Heaven Official's Blessing", exact: true })).toHaveAttribute('href', '#/read/2')
  await titles.getByRole('button', { name: 'Details: Signal' }).click()
  await expect(page.getByRole('dialog', { name: 'Signal' }).getByRole('link', { name: 'Watch', exact: true })).toHaveAttribute('href', '#/read/3?watch=1')
})
