import { expect, test } from '@playwright/test'

import {
  gate, holdShelfOf, keepBaiheUnreachable, mockShelf, placeholderAndShelf, rememberShelfCount, settle,
} from './continueShelfMocks'

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

test('line search shows the latest search when an earlier reply comes back last', async ({ page }) => {
  const hit = { drama_id: 1, idx: 0, title_en: 'Old', title_zh: null, zh: '旧', en: 'old answer' }
  let releaseFirst = () => undefined as void
  const firstHeld = new Promise<void>((r) => { releaseFirst = r })
  await page.route('**/api/library/search?*', async (route) => {
    const q = new URL(route.request().url()).searchParams.get('q')
    if (q === 'first') {
      await firstHeld
      return route.fulfill({ json: { items: [hit], count: 1 } })
    }
    return route.fulfill({ json: { items: [], count: 0 } })
  })
  await page.goto('/')
  await page.getByRole('radio', { name: 'Lines' }).check()
  const box = page.getByLabel('Search all lines')
  const search = page.getByRole('button', { name: 'Search', exact: true })
  await box.fill('first')
  await search.click()
  await box.fill('second')
  await search.click()
  await expect(page.getByTestId('search-count')).toHaveText('0 matches')
  const late = page.waitForResponse((r) => r.url().includes('q=first'))
  releaseFirst()
  await late
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 50))))
  await expect(page.getByTestId('search-count')).toHaveText('0 matches')
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
  await box.getByRole('button', { name: "Add only" }).click()
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

const statsFailOnce = (page: import('@playwright/test').Page) => {
  let stats = 0
  return page.route('**/api/library/stats', (r) => (++stats === 1
    ? r.fulfill({ status: 500, json: { error: { code: 'internal', message: 'Something went wrong.' } } })
    : r.fallback()))
}

test('Continue tiles name the next step and open it; only a tile waiting on a check asks for its count', async ({ page }) => {
  const mock = await mockShelf(page, (id) => (id === 3 ? 3 : 0))
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  await expect(shelf.getByRole('listitem')).toHaveCount(4)
  const tiles: [string, string, string][] = [
    ['Read from page 12', "Heaven Official's Blessing", '#/read/2'],
    ['Translate 1,234 lines', 'Grandmaster of Demonic Cultivation', '#/drama/1/translate'],
    ['Check the 3 lines', 'Signal', '#/drama/3/review'],
    ['Watch', 'Harbor Lights', '#/watch/61'],
  ]
  for (const [label, title, href] of tiles) {
    const link = shelf.getByRole('link', { name: `${label}: ${title}`, exact: true })
    await expect(link).toHaveText(label)
    await expect(link).toHaveAttribute('href', href)
  }
  // The badge and the reading line stay as they were.
  await expect(shelf).toContainText('Reading · page 12 · 40%')
  expect(mock.asked).toEqual([3])

  // Quiet Stream comes on screen with More: asked then, and nothing needs a look, so it says Export.
  await shelf.getByRole('button', { name: 'More (1)' }).click()
  await expect(shelf.getByRole('link', { name: 'Export: Quiet Stream', exact: true })).toHaveAttribute('href', '#/drama/62/export')
  expect(mock.asked).toEqual([3, 62])
  await shelf.getByRole('button', { name: 'Less' }).click()
  await shelf.getByRole('button', { name: 'More (1)' }).click()
  await expect(shelf.getByRole('link', { name: 'Export: Quiet Stream', exact: true })).toBeVisible()
  expect(mock.asked).toEqual([3, 62])

  await shelf.getByRole('link', { name: 'Check the 3 lines: Signal' }).click()
  await expect(page).toHaveURL(/#\/drama\/3\/review$/)
})

test('a failed count request leaves Check the lines, with no error on the page', async ({ page }) => {
  const mock = await mockShelf(page, () => null)
  // "Check the lines" is also the label while the count loads: only what the page shows once the 500 is in counts.
  const failed = page.waitForEvent('requestfinished', (r) => r.url().includes('only=risk'))
  await page.goto('/')
  await failed
  await settle(page)
  expect(mock.answered).toEqual([3])
  const shelf = page.getByRole('region', { name: 'Continue' })
  const check = shelf.getByRole('link', { name: 'Check the lines: Signal', exact: true })
  await expect(check).toHaveAttribute('href', '#/drama/3/review')
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('a count that failed is asked again when the page reloads its data (Retry)', async ({ page }) => {
  let counts = 0
  await mockShelf(page, () => (++counts === 1 ? null : 3))
  // The stats line fails once, so the page offers Retry, which reloads every panel; with Baihe "unreachable" the
  // page does not reload by itself first.
  await statsFailOnce(page)
  await keepBaiheUnreachable(page)
  const failed = page.waitForEvent('requestfinished', (r) => r.url().includes('only=risk'))
  await page.goto('/')
  await failed
  const shelf = page.getByRole('region', { name: 'Continue' })
  await expect(shelf.getByRole('link', { name: 'Check the lines: Signal', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(shelf.getByRole('link', { name: 'Check the 3 lines: Signal', exact: true })).toBeVisible()
})

test('a reload keeps each count and its link until the new answer replaces it', async ({ page }) => {
  // Two tiles waiting on a check: Signal with 3 lines to look at, Harbor Lights with none (so Export).
  const mock = await mockShelf(page, (id) => (id === 3 ? 3 : 0))
  mock.rows = mock.rows.map((d) => (d.id === 61 ? { ...d, status: 'translated' } : d))
  await statsFailOnce(page)
  await keepBaiheUnreachable(page)
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  const signal = shelf.getByRole('link', { name: 'Check the 3 lines: Signal', exact: true })
  const harbor = shelf.getByRole('link', { name: 'Export: Harbor Lights', exact: true })
  await expect(signal).toHaveAttribute('href', '#/drama/3/review')
  await expect(harbor).toHaveAttribute('href', '#/drama/61/export')
  expect(mock.answered.sort()).toEqual([3, 61])

  const held = gate()
  mock.holdCount = held.shut
  mock.risk = (id) => (id === 3 ? 5 : 2)
  const list = gate()
  mock.holdList = list.shut
  const listAsked = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/library/dramas')
  await page.getByRole('button', { name: 'Retry' }).click()
  await listAsked
  await settle(page)
  await expect(signal).toHaveAttribute('href', '#/drama/3/review')
  await expect(harbor).toHaveAttribute('href', '#/drama/61/export')
  // The rows on screen are the ones this reload is about to replace, so no count is asked on them.
  expect(mock.asked).toHaveLength(2)
  list.open()
  await expect.poll(() => mock.asked.length).toBe(4)
  await settle(page)
  await expect(signal).toHaveAttribute('href', '#/drama/3/review')
  await expect(harbor).toHaveAttribute('href', '#/drama/61/export')
  held.open()
  await expect(shelf.getByRole('link', { name: 'Check the 5 lines: Signal', exact: true })).toHaveAttribute('href', '#/drama/3/review')
  await expect(shelf.getByRole('link', { name: 'Check the 2 lines: Harbor Lights', exact: true })).toHaveAttribute('href', '#/drama/61/review')
})

test('after a reload a title the filter hides says Open, with no count asked, until the filter is cleared', async ({ page }) => {
  const mock = await mockShelf(page, (id) => (id === 3 ? 3 : 0))
  await statsFailOnce(page)
  await keepBaiheUnreachable(page)
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  await expect(shelf.getByRole('link', { name: 'Check the 3 lines: Signal', exact: true })).toBeVisible()
  await page.getByLabel('Status', { exact: true }).selectOption('translated')
  const titles = page.getByRole('region', { name: 'Titles' })
  await expect(titles.getByRole('link', { name: 'Grandmaster of Demonic Cultivation', exact: true })).toHaveCount(0)
  await expect(titles.getByRole('link', { name: 'Signal', exact: true })).toBeVisible()

  // Signal is exported elsewhere; the reload's filtered list no longer has it.
  mock.rows = mock.rows.map((d) => (d.id === 3 ? { ...d, status: 'exported' } : d))
  const before = mock.asked.length
  await page.getByRole('button', { name: 'Retry' }).click()
  await expect(titles.getByRole('link', { name: 'Signal', exact: true })).toHaveCount(0)
  const open = shelf.getByRole('link', { name: 'Open: Signal', exact: true })
  await expect(open).toHaveAttribute('href', '#/drama/3')
  await expect(shelf.getByRole('listitem').filter({ hasText: 'Signal' })).toContainText('Exported')
  await settle(page)
  expect(mock.asked.slice(before)).not.toContain(3)

  await page.getByLabel('Status', { exact: true }).selectOption('')
  await expect(shelf.getByRole('link', { name: 'Watch: Signal', exact: true })).toHaveAttribute('href', '#/watch/3')
})

test('after a reload whose list failed, the next reload asks no count until its own list answers', async ({ page }) => {
  const mock = await mockShelf(page, (id) => (id === 3 ? 3 : 0))
  await statsFailOnce(page)
  await keepBaiheUnreachable(page)
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  await expect(shelf.getByRole('link', { name: 'Check the 3 lines: Signal', exact: true })).toBeVisible()
  const isList = (u: string) => new URL(u).pathname === '/api/library/dramas'

  mock.failList = true
  const failed = page.waitForResponse((r) => isList(r.url()) && r.status() === 500)
  await page.getByRole('button', { name: 'Retry' }).click()
  await failed
  // That reload's list failed, so its old rows are what it has: their count is asked again.
  await expect.poll(() => mock.asked).toEqual([3, 3])
  await expect(page.getByRole('button', { name: 'Retry' })).toBeVisible()

  // Signal is exported elsewhere; the next reload's list says so once it answers.
  mock.rows = mock.rows.map((d) => (d.id === 3 ? { ...d, status: 'exported' } : d))
  mock.failList = false
  const list = gate()
  mock.holdList = list.shut
  const before = mock.asked.length
  const listAsked = page.waitForRequest((r) => isList(r.url()))
  await page.getByRole('button', { name: 'Retry' }).click()
  await listAsked
  await settle(page)
  expect(mock.asked.slice(before)).toEqual([])
  list.open()
  await expect(shelf.getByRole('link', { name: 'Watch: Signal', exact: true })).toHaveAttribute('href', '#/watch/3')
  await settle(page)
  expect(mock.asked.slice(before)).not.toContain(3)
})

test('a load whose shelf requests failed keeps the remembered tile count', async ({ page }) => {
  await rememberShelfCount(page, 3)
  const fail = { status: 500, json: { error: { code: 'internal', message: 'Something went wrong.' } } }
  await page.route('**/api/library/continue', (r) => r.fulfill(fail))
  await page.route('**/api/library/recent', (r) => r.fulfill(fail))
  await keepBaiheUnreachable(page)
  const listed = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/library/dramas' && r.ok())
  await page.goto('/')
  await listed
  await expect(page.getByRole('alert')).toContainText('Retry')
  // The shelf stops waiting, and would store a count, in the render that puts the title list on screen.
  await expect(page.getByTestId('drama-count')).toBeVisible()
  await settle(page)
  expect(await page.evaluate(() => localStorage.getItem('baihe.pref.library.continue.count'))).toBe('3')
})

test('the shelf waits for the Library list instead of showing Open, and a search does not turn a tile back into Open', async ({ page }) => {
  // A viewer who had four things to continue last time: the placeholder holds the shelf's place meanwhile.
  await rememberShelfCount(page, 4)
  const mock = await mockShelf(page, () => 0)
  const list = gate()
  mock.holdList = list.shut
  const continued = page.waitForResponse('**/api/library/continue')
  const recent = page.waitForResponse('**/api/library/recent')
  await page.goto('/')
  await continued
  await recent
  await settle(page)
  const shelf = page.getByRole('region', { name: 'Continue' })
  await expect(page.locator('.continue-placeholder')).toBeVisible()
  await expect(shelf).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Open: Grandmaster of Demonic Cultivation' })).toHaveCount(0)
  list.open()
  const grandmaster = shelf.getByRole('link', { name: 'Translate 1,234 lines: Grandmaster of Demonic Cultivation', exact: true })
  await expect(grandmaster).toHaveAttribute('href', '#/drama/1/translate')
  await expect(page.locator('.continue-placeholder')).toHaveCount(0)

  await page.getByLabel('Search title or summary').fill('Signal')
  const titles = page.getByRole('region', { name: 'Titles' })
  await expect(titles.getByRole('link', { name: 'Grandmaster of Demonic Cultivation', exact: true })).toHaveCount(0)
  await expect(titles.getByRole('link', { name: 'Signal', exact: true })).toBeVisible()
  await expect(grandmaster).toBeVisible()
})

test('a list that fails still shows the shelf: work tiles say Open, reading tiles their page, one error banner', async ({ page }) => {
  await rememberShelfCount(page, 4)
  const mock = await mockShelf(page, () => 0)
  mock.failList = true
  await keepBaiheUnreachable(page)
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  await expect(shelf.getByRole('link', { name: 'Open: Grandmaster of Demonic Cultivation', exact: true })).toHaveAttribute('href', '#/drama/1')
  await expect(shelf.getByRole('link', { name: "Read from page 12: Heaven Official's Blessing", exact: true })).toHaveAttribute('href', '#/read/2')
  await expect(page.locator('.continue-placeholder')).toHaveCount(0)
  await expect(page.getByRole('alert')).toHaveCount(1)
  await expect(page.getByRole('alert')).toContainText('Retry')
})

test('a first-time visitor sees no placeholder while the shelf loads', async ({ page }) => {
  const mock = await mockShelf(page, () => 0)
  const list = gate()
  mock.holdList = list.shut
  const recent = page.waitForResponse('**/api/library/recent')
  await page.goto('/')
  await recent
  await settle(page)
  await expect(page.locator('.continue-placeholder')).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Continue' })).toHaveCount(0)
  list.open()
  await expect(page.getByRole('region', { name: 'Continue' }).getByRole('listitem')).toHaveCount(4)
})

test('More, Less and More again before a count answers asks for it once', async ({ page }) => {
  const mock = await mockShelf(page, (id) => (id === 3 ? 3 : 0))
  const count = gate()
  mock.holdCount = count.shut
  await page.goto('/')
  const shelf = page.getByRole('region', { name: 'Continue' })
  await expect(shelf.getByRole('listitem')).toHaveCount(4)
  await shelf.getByRole('button', { name: 'More (1)' }).click()
  await expect.poll(() => mock.asked).toEqual([3, 62])
  await shelf.getByRole('button', { name: 'Less' }).click()
  count.open()
  await expect.poll(() => [...mock.answered].sort()).toEqual([3, 62])
  await settle(page)
  await shelf.getByRole('button', { name: 'More (1)' }).click()
  await expect(shelf.getByRole('link', { name: 'Export: Quiet Stream', exact: true })).toBeVisible()
  expect(mock.asked).toEqual([3, 62])
})

test('cards say Watch for video and Listen for audio, opening the Watch page; Read stays for the rest', async ({ page }) => {
  await mockShelf(page, () => 1)
  await page.goto('/')
  // One line needing a look: the tile words it as the title page does.
  await expect(page.getByRole('region', { name: 'Continue' }).getByRole('link', { name: 'Check the line: Signal', exact: true }))
    .toHaveAttribute('href', '#/drama/3/review')
  const titles = page.getByRole('region', { name: 'Titles' })
  await expect(titles.getByRole('link', { name: 'Watch Signal', exact: true })).toHaveAttribute('href', '#/watch/3')
  await expect(titles.getByRole('link', { name: 'Listen Grandmaster of Demonic Cultivation', exact: true })).toHaveAttribute('href', '#/watch/1')
  // Blessing has a saved reading place, so its card says Continue; the list view names it by kind.
  await expect(titles.getByRole('link', { name: "Continue Heaven Official's Blessing", exact: true })).toHaveAttribute('href', '#/read/2')
  await titles.getByRole('radio', { name: 'List' }).check()
  await expect(titles.getByRole('link', { name: 'Watch Signal', exact: true })).toHaveAttribute('href', '#/watch/3')
  await expect(titles.getByRole('link', { name: "Read Heaven Official's Blessing", exact: true })).toHaveAttribute('href', '#/read/2')
  await titles.getByRole('button', { name: 'Details: Signal' }).click()
  await expect(page.getByRole('dialog', { name: 'Signal' }).getByRole('link', { name: 'Watch', exact: true })).toHaveAttribute('href', '#/watch/3')
})

// The placeholder is the shelf's own markup: its height follows the shelf's, rows, More and spacing included.
for (const [width, count, compact] of [[1280, 5, false], [1440, 1, false], [1280, 5, true]] as const) {
  test(`at ${width}px${compact ? ', compact spacing,' : ''} the placeholder for ${count} has the loaded shelf's height`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 })
    if (compact) await page.addInitScript(() => localStorage.setItem('baihe.density', 'compact'))
    const release = await holdShelfOf(page, count)
    await page.goto('/')
    if (compact) await expect(page.locator('html')).toHaveAttribute('data-density', 'compact')
    const h = await placeholderAndShelf(page, release)
    expect(Math.abs(h.shelf - h.placeholder), JSON.stringify(h)).toBeLessThanOrEqual(2)
  })
}
