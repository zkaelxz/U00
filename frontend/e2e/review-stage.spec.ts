import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'
import { openFoldFor } from './reviewFolds'

// The seeded API has dramas but no lines, so before every test this spec
// writes three lines for drama 3 straight into the throwaway library the test
// server uses (the same SQLite file), then drives the real API. Job and AI
// endpoints are mocked; structure edits (split, merge, add, delete) are real.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

test.beforeEach(() => {
  python(`
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=1, start=1.5, end=3.0, zh='再见朋友', en='', flag='uncertain', flag_note='check'),
    Line(idx=2, start=3.0, end=4.5, zh='谢谢', en='Thanks, friend'),
])
`)
})

const job = (status: string) => ({
  job_id: 'rj', status, progress: null, message: '', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: status === 'running' ? null : 2, updated_at: 1,
})

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

async function open(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(3)
}

// Make a row active by clicking its line number (not its translation, which edits).
async function activate(page: Page, n: number) {
  const row = rows(page).nth(n)
  await row.locator('.review-idx').click()
  await expect(row).toHaveAttribute('aria-current', 'true')
  return row
}

test('Esc cancels, details are lazy, Alt+Down jumps to flagged lines', async ({ page }) => {
  await open(page)
  const row = rows(page).nth(0)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('discard me')
  await row.getByLabel('Translation').press('Escape')
  await expect(row.getByTestId('line-en')).toContainText('Hello there')

  await expect(page.getByLabel('Speaker')).toHaveCount(0)
  await row.getByRole('button', { name: 'Edit details' }).click()
  await expect(row.getByLabel('Speaker')).toBeVisible()
  await expect(page.getByLabel('Speaker')).toHaveCount(1)
  await row.getByRole('button', { name: 'Edit details' }).click()
  await expect(page.getByLabel('Speaker')).toHaveCount(0)

  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('Alt+ArrowDown')
  await expect(page.locator('.review-line[data-flagged="true"]')).toBeFocused()
})

test('starts on the flagged line; J/K and arrows move the active line; ? lists the keys', async ({ page }) => {
  await open(page)
  await expect(rows(page).nth(1)).toHaveAttribute('aria-current', 'true')
  await page.locator('body').click({ position: { x: 1, y: 1 } })
  await page.keyboard.press('j')
  await expect(rows(page).nth(2)).toBeFocused()
  await expect(rows(page).nth(2)).toHaveAttribute('aria-current', 'true')
  await page.keyboard.press('k')
  await page.keyboard.press('ArrowUp')
  await expect(rows(page).nth(0)).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(rows(page).nth(0).getByLabel('Translation')).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(rows(page).nth(0)).toBeFocused()

  await page.keyboard.press('?')
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toHaveCount(0)
  await page.getByRole('button', { name: 'Shortcuts' }).click()
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toContainText('Save and edit the next line')
})

test('lists, filters, searches and edits a line against the real API', async ({ page }) => {
  await open(page)
  await expect(page.getByTestId('line-counts')).toContainText('3 in this view · 1 flagged · 1 untranslated')
  await expect(page.getByRole('radio', { name: 'All lines 3' })).toBeChecked()
  await expect(page.getByTestId('line-flag')).toContainText('Uncertain')
  await expect(page.getByTestId('page-label')).toHaveCount(0)

  await page.getByRole('radio', { name: 'Flagged 1' }).check()
  await expect(rows(page)).toHaveCount(1)
  await page.getByRole('radio', { name: 'Untranslated 1' }).check()
  await expect(rows(page)).toHaveCount(1)
  await page.getByRole('radio', { name: /All lines/ }).check()
  await expect(rows(page)).toHaveCount(3)

  // Search as you type (debounced), × clears.
  await page.getByLabel('Search lines').fill('Thanks')
  await expect(rows(page)).toHaveCount(1)
  await expect(page.getByTestId('search-count')).toHaveText('1 match')
  await page.getByRole('button', { name: 'Clear search' }).click()
  await expect(rows(page)).toHaveCount(3)
  await page.getByLabel('Search lines').fill('nothing like this')
  await expect(page.getByText('No lines match “nothing like this”.')).toBeVisible()
  await page.getByRole('button', { name: 'Clear search' }).click()

  // Click the English text, type, Enter saves and opens the next line's editor.
  const row = rows(page).nth(0)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Hello, you')
  await row.getByLabel('Translation').press('Enter')
  await expect(page.getByTestId('line-en').filter({ hasText: 'Hello, you' })).toBeVisible()
  await expect(rows(page).nth(1).getByLabel('Translation')).toBeFocused()
  await rows(page).nth(1).getByLabel('Translation').press('Escape')

  // Ctrl+S saves and keeps the editor open; a second Ctrl+S sends nothing stale.
  const edited = rows(page).nth(0)
  await edited.getByTestId('line-en').click()
  await edited.getByLabel('Translation').fill('Hello there')
  await edited.getByLabel('Translation').press('Control+s')
  await expect(edited.getByLabel('Translation')).toBeVisible()
  await edited.getByLabel('Translation').press('Control+s')
  await expect(edited.getByTestId('line-conflict')).toHaveCount(0)
  await edited.getByLabel('Translation').press('Escape')
  await expect(page.getByTestId('line-en').filter({ hasText: 'Hello there' })).toBeVisible()

  // Dismiss the flag from the active line's toolbar.
  const flagged = await activate(page, 1)
  await flagged.getByRole('button', { name: 'Dismiss flag' }).click()
  await expect(page.getByTestId('line-flag')).toHaveCount(0)
})

test('moving to another line saves a dirty draft first', async ({ page }) => {
  const saves: unknown[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/api\/lines\/dramas\/3\/lines\/\d+$/.test(r.url())) saves.push(r.postDataJSON())
  })
  await open(page)
  const row = rows(page).nth(0)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Hi')
  await rows(page).nth(2).locator('.review-idx').click()
  await expect(rows(page).nth(2)).toHaveAttribute('aria-current', 'true')
  await expect(page.getByTestId('line-en').filter({ hasText: /^Hi$/ })).toBeVisible()
  expect(saves).toEqual([{ en: 'Hi', expected: { en: 'Hello there' } }])
})

test('dismissing the flag under the Flagged filter saves the open draft', async ({ page }) => {
  const saves: unknown[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && /\/api\/lines\/dramas\/3\/lines\/\d+$/.test(r.url())) saves.push(r.postDataJSON())
  })
  await open(page)
  await page.getByRole('radio', { name: /^Flagged/ }).check()
  await expect(rows(page)).toHaveCount(1)
  const row = rows(page).nth(0)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Goodbye, friend')
  await row.getByRole('button', { name: 'Dismiss flag' }).click()
  await expect(page.getByText('No flagged lines. Nice.')).toBeVisible()
  await page.getByRole('radio', { name: /^All lines/ }).check()
  await expect(page.getByTestId('line-en').filter({ hasText: 'Goodbye, friend' })).toBeVisible()
  expect(saves).toEqual([{ en: 'Goodbye, friend', expected: { en: '' } }])
})

test('a draft pushed out of view by a reload gets a banner; Discard lets you move', async ({ page }) => {
  let reloaded = false
  let done = false
  await page.route('**/api/review/dramas/3/lines?*', (route) => {
    const empty = { lines: [], page: 1, page_size: 40, total: 0, flagged_count: 0, untranslated_count: 1 }
    if (reloaded && route.request().url().includes('only=flagged')) return route.fulfill({ json: empty })
    return route.continue()
  })
  await page.route('**/api/lines/dramas/3/lines/*', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'This line changed since you loaded it.' } } }))
  await page.route('**/api/review-jobs/dramas/3/flag', (route) => route.fulfill({
    json: { job_id: 'rj', drama_id: 3, kind: 'flag', engine: 'x', model: null, line_count: 3 },
  }))
  await page.route('**/api/jobs/rj', (route) => route.fulfill({ json: job(done ? 'done' : 'running') }))
  await open(page)
  await page.getByRole('radio', { name: /^Flagged/ }).check()
  await expect(rows(page)).toHaveCount(1)
  await rows(page).nth(0).getByTestId('line-en').click()
  await rows(page).nth(0).getByLabel('Translation').fill('Kept safe')

  // A finished job reloads the list and the edited line leaves the Flagged view.
  reloaded = true
  await page.getByRole('button', { name: 'Flag lines for a second look' }).click()
  done = true
  const banner = page.getByTestId('hidden-edit')
  await expect(banner).toContainText('Your edit to #2 is outside this view.')

  // Moving is refused (the save 409s) and says where the draft is.
  await page.getByRole('radio', { name: /^All lines/ }).click()
  await expect(banner).toContainText('It changed elsewhere')
  await expect(page.getByRole('status').filter({ hasText: 'Save or discard your edit to #2 first' })).toBeVisible()
  await expect(page.getByRole('radio', { name: /^Flagged/ })).toBeChecked()

  await banner.getByRole('button', { name: 'Discard' }).click()
  await expect(banner).toHaveCount(0)
  await page.getByRole('radio', { name: /^All lines/ }).check()
  await expect(rows(page)).toHaveCount(3)
})

test('a double click on a structure confirm sends one edit', async ({ page }) => {
  let merges = 0
  await page.route('**/api/restructure/dramas/3/merge', async (route) => {
    merges += 1
    await new Promise((r) => setTimeout(r, 300))
    return route.continue()
  })
  await open(page)
  await activate(page, 0)
  await page.keyboard.press('m')
  await page.getByRole('button', { name: /^Merge #/ }).dblclick()
  await expect(rows(page)).toHaveCount(2)
  expect(merges).toBe(1)
})

test('opening a structure form from the sheet saves the open draft first', async ({ page }) => {
  await open(page)
  const row = rows(page).nth(0)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Typed before adding')
  await row.getByRole('button', { name: 'More actions for line 1' }).click()
  await page.getByRole('dialog', { name: 'Line #1' }).getByRole('button', { name: 'Add line after…' }).click()
  const add = page.getByRole('dialog', { name: 'Add a line after #1' })
  await add.getByLabel('Translation').fill('Added')
  await add.getByRole('button', { name: 'Add after #1' }).click()
  await expect(rows(page)).toHaveCount(4)
  await expect(page.getByTestId('line-en').filter({ hasText: 'Typed before adding' })).toBeVisible()
})

test('merge and add wait for the All lines view', async ({ page }) => {
  await open(page)
  await page.getByLabel('Search lines').fill('谢谢')
  await expect(rows(page)).toHaveCount(1)
  const row = await activate(page, 0)
  await expect(row.getByRole('button', { name: 'Merge ↓' })).toBeDisabled()
  await row.getByRole('button', { name: /More actions/ }).click()
  await expect(page.getByRole('button', { name: /Add line after/ })).toBeDisabled()
})

test('a stale edit shows the changed-elsewhere message and keeps the draft', async ({ page }) => {
  await page.route('**/api/lines/dramas/3/lines/*', (route) =>
    route.fulfill({
      status: 409,
      json: { error: { code: 'conflict', message: 'This line changed since you loaded it.' } },
    }),
  )
  await open(page)
  const row = rows(page).nth(2)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Thank you')
  await row.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(row.getByTestId('line-conflict')).toContainText('changed elsewhere')
  await expect(row.getByLabel('Translation')).toHaveValue('Thank you')
  // A failed save cancels the move: the draft stays open.
  await rows(page).nth(0).locator('.review-idx').click()
  await expect(row.getByLabel('Translation')).toHaveValue('Thank you')
})

test('find and replace previews then applies and shows stale ids', async ({ page }) => {
  await page.route('**/api/lines/dramas/3/find-replace/apply', async (route) => {
    const id = route.request().postDataJSON().matches[0].id
    await route.fulfill({ json: { applied: 0, stale: 1, applied_ids: [], stale_ids: [id] } })
  })
  await open(page)
  await page.getByRole('button', { name: 'Replace…' }).click()
  await page.getByLabel('Find', { exact: true }).fill('friend')
  await page.getByLabel('Replace with').fill('pal')
  await page.getByRole('button', { name: 'Preview' }).click()
  await expect(page.getByTestId('fr-matches')).toContainText('Thanks, pal')
  await page.getByRole('button', { name: /Apply 1 change/ }).click()
  await expect(page.getByTestId('fr-result')).toContainText('Applied 0; skipped 1')
  await expect(page.getByTestId('fr-stale')).toContainText('Skipped: #3')
})

test('a finished review job refetches the lines', async ({ page }) => {
  let done = false
  let lineFetches = 0
  await page.route('**/api/review-jobs/dramas/3/flag', (route) => route.fulfill({
    json: { job_id: 'rj', drama_id: 1, kind: 'flag', engine: 'x', model: null, line_count: 3 },
  }))
  await page.route('**/api/jobs/rj', (route) => route.fulfill({ json: job(done ? 'done' : 'running') }))
  await page.route('**/api/review/dramas/3/lines?*', (route) => {
    lineFetches += 1
    return route.continue()
  })
  await open(page)
  const before = lineFetches
  await page.getByRole('button', { name: 'Flag lines for a second look' }).click()
  await expect(page.getByTestId('job-status')).toContainText('Running')
  done = true
  await expect(page.getByTestId('job-status')).toContainText('Done')
  await expect.poll(() => lineFetches).toBeGreaterThan(before)
})

const improveOut = (en: string) => ({
  line_id: 1, current_en: en, suggestion: 'Hi there', changed: true, engine: 'x', model: null,
})

async function openAi(page: Page, n: number, item: 'Improve translation' | 'Why this?') {
  const row = await activate(page, n)
  await row.getByRole('button', { name: item, exact: true }).click()
  return row
}

test('improve, use this, and the row shows the saved value', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/improve', async (route) => {
    expect(route.request().postDataJSON().issue).toBe('too stiff')
    await route.fulfill({ json: { ...improveOut('Hello there'), line_id: Number(route.request().url().split('/lines/')[1].split('/')[0]) } })
  })
  await open(page)
  const row = await openAi(page, 0, 'Improve translation')
  await row.getByLabel('What to fix (optional)').fill('too stiff')
  await row.getByRole('button', { name: 'Suggest' }).click()
  await expect(row.getByTestId('line-ai-suggestion')).toHaveText('Hi there')
  await expect(row.getByTestId('line-en')).toContainText('Hello there')
  await row.getByRole('button', { name: 'Use this' }).click()
  await expect(page.getByTestId('line-en').filter({ hasText: 'Hi there' })).toBeVisible()
  await expect(page.getByTestId('line-ai-panel')).toHaveCount(0)
})

test('improve needs a translation; a kept line says so; why-this explains', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/improve', (route) =>
    route.fulfill({ json: { ...improveOut('Thanks, friend'), suggestion: 'Thanks, friend', changed: false } }))
  await page.route('**/api/line-ai/dramas/3/lines/*/explain', (route) =>
    route.fulfill({ json: { line_id: 1, explanation: 'Friendly register.', engine: 'x', model: null } }))
  await open(page)
  const untranslated = await activate(page, 1)
  await expect(untranslated.getByRole('button', { name: 'Improve (needs a translation first)' })).toBeDisabled()

  const row = await openAi(page, 2, 'Improve translation')
  await row.getByRole('button', { name: 'Suggest' }).click()
  await expect(row.getByTestId('line-ai-result')).toContainText('kept this line')
  await expect(row.getByRole('button', { name: 'Use this' })).toHaveCount(0)
  await row.getByRole('button', { name: 'Close' }).click()

  await openAi(page, 0, 'Why this?')
  await expect(page.getByTestId('line-ai-explanation')).toHaveText('Friendly register.')
  await page.getByRole('button', { name: 'Hide explanation' }).click()
  await expect(page.getByTestId('line-ai-panel')).toHaveCount(0)
})

test('no configured key shows a short message pointing to Settings', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/explain', (route) =>
    route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: 'no key' } } }))
  await open(page)
  const row = await openAi(page, 0, 'Why this?')
  await expect(row.getByTestId('line-ai-unavailable')).toContainText('Settings')
})

test('why-this offers Try again after the AI was busy', async ({ page }) => {
  let n = 0
  await page.route('**/api/line-ai/dramas/3/lines/*/explain', (route) => {
    n += 1
    return n === 1
      ? route.fulfill({ status: 429, json: { error: { code: 'rate_limited', message: 'busy' } } })
      : route.fulfill({ json: { line_id: 1, explanation: 'Second time lucky.', engine: 'x', model: null } })
  })
  await open(page)
  const row = await openAi(page, 0, 'Why this?')
  await row.getByTestId('line-ai-retry').click()
  await expect(row.getByTestId('line-ai-explanation')).toHaveText('Second time lucky.')
  await expect(row.getByTestId('line-ai-retry')).toHaveCount(0)
  expect(n).toBe(2)
})

test('applying a suggestion over a concurrent edit shows the conflict', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/improve', (route) =>
    route.fulfill({ json: improveOut('Thanks, friend') }))
  await page.route('**/api/lines/dramas/3/lines/*', (route) => {
    expect(route.request().postDataJSON()).toEqual({ en: 'Hi there', expected: { en: 'Thanks, friend' } })
    return route.fulfill({
      status: 409,
      json: { error: { code: 'conflict', message: 'This line changed since you loaded it.' } },
    })
  })
  await open(page)
  const row = await openAi(page, 2, 'Improve translation')
  await row.getByRole('button', { name: 'Suggest' }).click()
  await row.getByRole('button', { name: 'Use this' }).click()
  await expect(row.getByTestId('line-conflict')).toContainText('changed elsewhere')
})

// ---- structure edits (real API) ----

function captureBodies(page: Page, pattern: RegExp) {
  const bodies: Record<string, unknown>[] = []
  page.on('request', (r) => {
    if (r.method() === 'POST' && pattern.test(r.url())) bodies.push(r.postDataJSON())
  })
  return bodies
}

test('split a line from the sheet, then merge it back', async ({ page }) => {
  const bodies = captureBodies(page, /\/api\/restructure\//)
  await open(page)
  const ids = await rows(page).evaluateAll((els) => els.map((e) => Number(e.getAttribute('data-line-id'))))

  await rows(page).nth(1).getByRole('button', { name: 'More actions for line 2' }).click()
  const sheet = page.getByRole('dialog', { name: 'Line #2' })
  await sheet.getByRole('button', { name: 'Split line…' }).click()
  const split = page.getByRole('dialog', { name: 'Split line #2' })
  await split.getByLabel('Break after (chars)').fill('2')
  await expect(split.getByTestId('split-preview')).toContainText('再见')
  await expect(split.getByTestId('split-preview')).toContainText('朋友')
  await split.getByRole('button', { name: 'Split line' }).click()

  await expect(rows(page)).toHaveCount(4)
  await expect(page.getByRole('status').filter({ hasText: /^Split #/ })).toContainText('Undo in Records → Line history.')
  expect(bodies[0]).toEqual({ expected_line_ids: ids, at_char: 2, expected_zh: '再见朋友' })
  // The new second piece is the active line.
  await expect(rows(page).nth(2)).toHaveAttribute('aria-current', 'true')
  await expect(rows(page).nth(2).locator('.review-zh')).toHaveText('朋友')

  // M on the first piece merges it with the next line again.
  await activate(page, 1)
  await page.keyboard.press('m')
  const merge = page.getByRole('dialog', { name: /Merge #\d+ with next/ })
  await expect(merge.getByTestId('merge-preview')).toContainText('再见朋友')
  await merge.getByRole('button', { name: /^Merge #/ }).click()
  await expect(rows(page)).toHaveCount(3)
  await expect(rows(page).nth(1).locator('.review-zh')).toHaveText('再见朋友')
  const merged = bodies[1] as { line_ids: number[]; expected_line_ids: number[] }
  expect(merged.line_ids[0]).toBe(ids[1])
  expect(merged.expected_line_ids).toHaveLength(4)
})

test('delete is two-step and add fills the gap', async ({ page }) => {
  const bodies = captureBodies(page, /\/api\/restructure\//)
  await open(page)
  await activate(page, 0)
  await page.keyboard.press('a')
  const add = page.getByRole('dialog', { name: 'Add a line after #1' })
  await expect(add.getByLabel('Start (s)')).toHaveValue('1.5')
  await add.getByLabel('Translation').fill('A new line')
  await add.getByRole('button', { name: 'Add after #1' }).click()
  await expect(rows(page)).toHaveCount(4)
  expect(bodies[0]).toMatchObject({ after_line_id: expect.any(Number), start: 1.5, en: 'A new line' })

  const target = rows(page).filter({ hasText: 'A new line' })
  await target.getByRole('button', { name: /More actions for line/ }).click()
  const sheet = page.getByRole('dialog', { name: /^Line #/ })
  await sheet.getByRole('button', { name: 'Delete line…' }).click()
  const confirm = sheet.getByRole('button', { name: /^Confirm delete #/ })
  await expect(confirm).toBeFocused()
  await confirm.click()
  await expect(rows(page)).toHaveCount(3)
  expect(bodies[1]).toMatchObject({ confirm: true })
  await expect(page.getByTestId('line-en').filter({ hasText: 'A new line' })).toHaveCount(0)
})

test('structure edits are refused with a plain message when the lines changed', async ({ page }) => {
  await page.route('**/api/restructure/dramas/3/merge', (route) =>
    route.fulfill({ status: 409, json: { error: { code: 'conflict', message: "This drama's lines changed since you loaded them" } } }))
  await open(page)
  await activate(page, 0)
  await page.keyboard.press('m')
  await page.getByRole('button', { name: /^Merge #/ }).click()
  await expect(page.getByTestId('structure-error')).toHaveText('Lines changed since this page loaded. Reload and try again.')
  await expect(rows(page)).toHaveCount(3)
})

test('structure edits wait while a job runs on the drama', async ({ page }) => {
  await page.route('**/api/jobs', (route) =>
    route.fulfill({ json: { items: [{ ...job('running'), job_id: 'translate_3' }], count: 1 } }))
  await open(page)
  const row = await activate(page, 0)
  await expect(row.getByRole('button', { name: 'Split…' })).toBeDisabled()
  await expect(row).toContainText('A job is running on this drama.')
  // Text edits stay allowed.
  await row.getByTestId('line-en').click()
  await expect(row.getByLabel('Translation')).toBeVisible()
})

test('re-segment previews, then needs the typed word', async ({ page }) => {
  await page.route('**/api/restructure/dramas/3/resegment/preview', (route) => route.fulfill({
    json: {
      drama_id: 3, source_line_ids: [1, 2, 3], line_count_before: 3, line_count_after: 4,
      changed: [{ line_id: 2, idx: 2, zh: '再见朋友', pieces: ['再见', '朋友'] }],
      translated: 0, flagged: 1, notes: 0, needs_confirm: true,
    },
  }))
  let started: Record<string, unknown> | null = null
  await page.route('**/api/restructure/dramas/3/resegment', (route) => {
    started = route.request().postDataJSON()
    return route.fulfill({ json: { job_id: 'rj', drama_id: 3 } })
  })
  await page.route('**/api/jobs/rj', (route) => route.fulfill({ json: job('done') }))
  await open(page)
  await openFoldFor(page, 'Structure')
  await page.locator('summary', { hasText: /^Structure/ }).click()
  await page.getByRole('button', { name: 'Preview re-segmentation' }).click()
  await expect(page.getByTestId('resegment-preview')).toContainText('3 → 4 lines; 1 change; 0 translated, 1 flagged, 0 notes would be split')
  const run = page.getByRole('button', { name: 'Re-segment lines' })
  await expect(run).toBeDisabled()
  await page.getByLabel('Type resegment to confirm').fill('resegment')
  await run.click()
  await expect(page.getByTestId('job-status')).toContainText('Done')
  expect(started).toEqual({ expected_line_ids: [1, 2, 3], confirm: true, use_llm: false })
})

test('restore a line-history snapshot with the typed word', async ({ page }) => {
  await page.route('**/api/review/dramas/3/history', (route) =>
    route.fulfill({ json: [{ id: 7, drama_id: 3, label: 'before merge', created_at: '2026-09-29T10:00:00' }] }))
  let body: Record<string, unknown> | null = null
  await page.route('**/api/restructure/dramas/3/history/7/restore', (route) => {
    body = route.request().postDataJSON()
    return route.fulfill({ json: { history_id: 7, line_ids: [1, 2, 3] } })
  })
  await open(page)
  const ids = await rows(page).evaluateAll((els) => els.map((e) => Number(e.getAttribute('data-line-id'))))
  await openFoldFor(page, 'Records')
  await page.locator('summary').filter({ has: page.locator('.section-title', { hasText: /^Records$/ }) }).click()
  await page.getByTestId('history-list').getByRole('button', { name: 'Restore…' }).click()
  await page.getByLabel('Type restore to confirm').fill('restore')
  await page.getByRole('button', { name: 'Restore snapshot' }).click()
  await expect(page.getByTestId('restore-status')).toContainText('Restored “before merge”')
  expect(body).toEqual({ expected_line_ids: ids })
})

// ---- player (Slice 52 Range endpoint) ----

test('without media there are no play controls', async ({ page }) => {
  await open(page)
  await activate(page, 0)
  await expect(page.getByRole('group', { name: 'Player' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '▶ Play' })).toHaveCount(0)
})

function withAudio() {
  python(`
import os, wave, struct, math
p = os.path.join(db.drama_dir(3), 'e2e.wav')
with wave.open(p, 'wb') as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
    w.writeframes(b''.join(struct.pack('<h', int(2000 * math.sin(i / 6))) for i in range(8000 * 6)))
db.update_drama(3, audio_filename='e2e.wav')
`)
}

// A 6-second WebM for drama 3's source video, written with OpenCV when it's
// installed (it bundles an ffmpeg with a VP8 encoder); false otherwise.
function withVideo(): boolean {
  try {
    python(`
import os, cv2, numpy as np
p = os.path.join(db.drama_dir(3), 'e2e.webm')
w = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*'VP80'), 10, (160, 90))
assert w.isOpened()
for i in range(60):
    w.write(np.full((90, 160, 3), (i * 4) % 255, np.uint8))
w.release()
db.update_drama(3, source_video_filename='e2e.webm')
`)
    return true
  } catch {
    return false
  }
}

test('with audio, Play line plays exactly the line', async ({ page }) => {
  withAudio()
  await open(page)
  await expect(page.getByRole('group', { name: 'Player' })).toBeVisible()
  await expect(page.getByTestId('player-time')).toContainText('/ 0:06')
  const row = await activate(page, 1)
  await row.getByRole('button', { name: '▶ Play' }).click()
  const audio = page.locator('.review-player audio')
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.currentTime)).toBeGreaterThan(1.5)
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.paused), { timeout: 6000 }).toBe(true)
  const t = await audio.evaluate((a: HTMLAudioElement) => a.currentTime)
  expect(t).toBeGreaterThanOrEqual(2.9)
  expect(t).toBeLessThan(3.3)
})

test('the player is open by default: seek bar, jump to time, go to the selected line', async ({ page }) => {
  withAudio()
  await open(page)
  const player = page.getByRole('group', { name: 'Player' })
  const audio = page.locator('.review-player audio')
  const seek = player.getByRole('slider', { name: 'Seek' })
  await expect(seek).toBeVisible()
  await expect(page.getByTestId('player-time')).toContainText('/ 0:06')
  await expect(seek).toBeEnabled()

  await player.getByLabel('Jump to time').fill('0:04.5')
  await player.getByRole('button', { name: 'Jump' }).click()
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.currentTime)).toBeCloseTo(4.5, 1)
  await expect(page.getByTestId('player-time')).toContainText('0:04.50')

  await player.getByLabel('Jump to time').fill('1:99')
  await player.getByRole('button', { name: 'Jump' }).click()
  await expect(player.getByRole('alert')).toContainText('Type a time like')

  await activate(page, 1)
  await player.getByRole('button', { name: 'Go to line #2' }).click()
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.currentTime)).toBeCloseTo(1.5, 1)
  await expect(page.getByTestId('player-line')).toHaveText('Line #2')

  await seek.fill('3.2')
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.currentTime)).toBeCloseTo(3.2, 1)

  // Folding it away keeps the strip (and the sound); the choice is remembered.
  await player.getByRole('button', { name: 'Hide player' }).click()
  await expect(seek).toBeHidden()
  await expect(player.getByRole('button', { name: 'Play', exact: true })).toBeVisible()
  await page.reload()
  await expect(player.getByRole('button', { name: 'Show player' })).toBeVisible()
  await expect(seek).toBeHidden()
})

test('subtitles follow the playhead, switch language, and pick up an edit', async ({ page }) => {
  withAudio()
  await open(page)
  const player = page.getByRole('group', { name: 'Player' })
  const caption = page.getByTestId('player-caption')
  const audio = page.locator('.review-player audio')
  const seekTo = async (t: number) => {
    await player.getByLabel('Jump to time').fill(String(t))
    await player.getByRole('button', { name: 'Jump' }).click()
  }
  // Wait for the English track's cues before seeking into them.
  await expect
    .poll(() => audio.evaluate((a: HTMLAudioElement) => a.textTracks[0]?.cues?.length ?? 0))
    .toBe(3)
  await seekTo(0.5)
  await expect(caption).toHaveText('Hello there')
  await seekTo(3.5)
  await expect(caption).toHaveText('Thanks, friend')

  await player.getByLabel('Subtitles').selectOption({ label: 'Original' })
  await expect(caption).toHaveText('谢谢')
  // Both: the translation over the original, one cue per line.
  await player.getByLabel('Subtitles').selectOption({ label: 'Both' })
  await expect.poll(() => caption.evaluate((p) => p.textContent)).toBe('Thanks, friend\n谢谢')
  await seekTo(0.5)
  await expect.poll(() => caption.evaluate((p) => p.textContent)).toBe('Hello there\n你好')
  await seekTo(3.5)
  await player.getByLabel('Subtitles').selectOption({ label: 'English' })
  await expect(caption).toHaveText('Thanks, friend')

  const row = rows(page).nth(2)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Much obliged')
  await row.getByLabel('Translation').press('Enter')
  await expect(row.getByTestId('line-en')).toContainText('Much obliged')
  await seekTo(3.6)
  await expect(caption).toHaveText('Much obliged')

  await player.getByLabel('Subtitles').selectOption({ label: 'Off' })
  await expect(caption).toHaveCount(0)
  await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.textTracks.length)).toBe(0)
})

test('with a source video, it shows by default with English subtitles drawn on it', async ({ page }) => {
  test.skip(!withVideo(), 'OpenCV is not installed, so there is no test video')
  await open(page)
  // Wide screens: the video, seek bar and subtitles sit in their own card beside the lines.
  const card = page.getByRole('complementary', { name: 'Video with subtitles' })
  const video = card.locator('video.review-video')
  await expect(video).toBeVisible()
  await expect(page.getByTestId('player-time')).toContainText('/ 0:06')
  await expect
    .poll(() => video.evaluate((v: HTMLVideoElement) => ({ mode: v.textTracks[0]?.mode, cues: v.textTracks[0]?.cues?.length ?? 0 })))
    .toEqual({ mode: 'showing', cues: 3 })
  await expect(video.locator('track')).toHaveAttribute('src', /\/api\/reader\/dramas\/3\/captions\/English\?v=\d+$/)
  const player = page.getByRole('group', { name: 'Player' })
  await card.getByLabel('Jump to time').fill('0.5')
  await card.getByRole('button', { name: 'Jump' }).click()
  await card.getByLabel('Subtitles').selectOption({ label: 'English' })
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.textTracks[0]?.activeCues?.length ?? 0)).toBe(1)
  await page.screenshot({ path: 'test-results/review-player-desktop.png' })
  // Loop is an on/off switch in the player strip.
  const loop = player.getByRole('switch', { name: 'Loop line' })
  await expect(loop).toHaveAttribute('aria-checked', 'false')
  await loop.click()
  await expect(loop).toHaveAttribute('aria-checked', 'true')
  await loop.click()
  // Hiding the video folds the card away; the strip (and the sound) stay.
  await player.getByRole('button', { name: 'Hide player' }).click()
  await expect(video).toBeHidden()
  await expect(card).toBeHidden()
  await player.getByRole('button', { name: 'Show player' }).click()
  await expect(video).toBeVisible()
})

test('on a tablet the video card sits beside the lines, with no sideways scroll', async ({ page }) => {
  test.skip(!withVideo(), 'OpenCV is not installed, so there is no test video')
  await page.setViewportSize({ width: 820, height: 1180 })
  await open(page)
  const card = page.getByRole('complementary', { name: 'Video with subtitles' })
  await expect(card.locator('video.review-video')).toBeVisible()
  await expect(card.getByLabel('Subtitles')).toBeVisible()
  const lines = await page.locator('.review-lines').boundingBox()
  const box = await card.boundingBox()
  expect(lines && box && box.x >= lines.x + lines.width).toBe(true)
  const { scroll, client } = await page.evaluate(() => ({
    scroll: document.documentElement.scrollWidth,
    client: document.documentElement.clientWidth,
  }))
  expect(scroll).toBeLessThanOrEqual(client)
})

test('crossing the phone width keeps the same video, its position and its play state', async ({ page }) => {
  test.skip(!withVideo(), 'OpenCV is not installed, so there is no test video')
  await page.setViewportSize({ width: 1024, height: 800 })
  await open(page)
  const video = page.locator('video.review-video')
  const playButton = page.locator('.review-player button.review-play')
  const jumpTo = async (t: number) => {
    await page.getByLabel('Jump to time').fill(String(t))
    await page.getByRole('button', { name: 'Jump' }).click()
  }
  await expect(page.getByTestId('player-time')).toContainText('/ 0:06')
  // Tag the element: a remount would give a fresh <video> without the tag.
  await video.evaluate((v) => ((v as HTMLVideoElement & { e2eTag?: string }).e2eTag = 'first'))
  const state = () =>
    video.evaluate((v: HTMLVideoElement & { e2eTag?: string }) => ({
      tag: v.e2eTag,
      paused: v.paused,
      time: v.currentTime,
      docked: !!v.closest('.review-player-dock'),
    }))

  // Playing: turn to a phone; it keeps playing from where it was.
  await jumpTo(1)
  await playButton.click()
  await expect.poll(async () => (await state()).time).toBeGreaterThan(1.2)
  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(async () => (await state()).docked).toBe(true)
  const turned = await state()
  expect(turned.tag).toBe('first')
  expect(turned.paused).toBe(false)
  expect(turned.time).toBeGreaterThan(1.2)
  await expect(playButton).toHaveAccessibleName('Pause')

  // Paused after a seek: back to the wide layout; still paused at that spot.
  await playButton.click()
  await expect.poll(async () => (await state()).paused).toBe(true)
  await jumpTo(4)
  await expect.poll(async () => (await state()).time).toBeCloseTo(4, 1)
  await page.setViewportSize({ width: 1024, height: 800 })
  await expect.poll(async () => (await state()).docked).toBe(false)
  const back = await state()
  expect(back.tag).toBe('first')
  expect(back.paused).toBe(true)
  expect(back.time).toBeCloseTo(4, 1)
  await expect(playButton).toHaveAccessibleName('Play')
  await expect(page.getByTestId('player-time')).toContainText('0:04.00')
  await expect(video).toBeVisible()
})

// ---- phone layout (390x844, touch) ----

test.describe('phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true })

  test('the open player fits the width with 44px controls', async ({ page }) => {
    withAudio()
    withVideo()
    await open(page)
    await expect(page.getByRole('slider', { name: 'Seek' })).toBeVisible()
    await page.screenshot({ path: 'test-results/review-player-phone.png' })
    await activate(page, 0)
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    expect(scroll).toBeLessThanOrEqual(client)
    const small = await page
      .locator('.review-player button:not(.toggle), .review-player select, .review-player input[type=text], .review-player input[type=range]')
      .evaluateAll((els) =>
        els
          .filter((e) => (e as HTMLElement).offsetParent !== null)
          .map((e) => ({ h: e.getBoundingClientRect().height, t: (e.textContent || e.getAttribute('aria-label') || '').trim() }))
          .filter(({ h }) => h < 44),
      )
    expect(small).toEqual([])
    // The Loop switch draws a 24px track; its ::after hit area (index.css) makes the target 44px+.
    const loopHit = await page.getByRole('switch', { name: 'Loop line' }).evaluate((e) => {
      const after = getComputedStyle(e, '::after')
      return e.getBoundingClientRect().height - parseFloat(after.top) - parseFloat(after.bottom)
    })
    expect(loopHit).toBeGreaterThanOrEqual(44)
    await page.screenshot({ path: 'test-results/review-player-phone.png' })
  })

  test('fits the width, keeps 44px targets, and edits from the bottom bar', async ({ page }) => {
    await open(page)
    const { scroll, client } = await page.evaluate(() => ({
      scroll: document.documentElement.scrollWidth,
      client: document.documentElement.clientWidth,
    }))
    expect(scroll).toBeLessThanOrEqual(client)
    // (i) help buttons and switches are left out: they get the shared ::after hit area (index.css).
    const small = await page.locator('.stage-review button:not(.link, .review-en, .field-help-btn, .toggle), .stage-review .review-chip').evaluateAll((els) =>
      els
        .filter((e) => (e as HTMLElement).offsetParent !== null)
        .map((e) => ({ h: e.getBoundingClientRect().height, w: e.getBoundingClientRect().width, t: (e.textContent ?? '').trim() }))
        .filter(({ h, w }) => h < 44 || w < 44),
    )
    expect(small).toEqual([])

    // Tap a row to make it active; the bottom bar edits it.
    await rows(page).nth(0).locator('.review-zh').tap()
    await expect(rows(page).nth(0)).toHaveAttribute('aria-current', 'true')
    const bar = page.getByRole('toolbar', { name: 'Line actions' })
    await bar.getByRole('button', { name: 'Edit' }).tap()
    await rows(page).nth(0).getByLabel('Translation').fill('Hi from the phone')
    await bar.getByRole('button', { name: 'Save & next' }).tap()
    await expect(page.getByTestId('line-en').filter({ hasText: 'Hi from the phone' })).toBeVisible()
    await expect(rows(page).nth(1).getByLabel('Translation')).toBeVisible()
    await bar.getByRole('button', { name: 'Cancel' }).tap()

    // The ⋯ sheet opens with 48px rows.
    await rows(page).nth(2).getByRole('button', { name: 'More actions for line 3' }).tap()
    const sheet = page.getByRole('dialog', { name: 'Line #3' })
    await expect(sheet).toBeVisible()
    const heights = await sheet.locator('.sheet-menu button').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().height))
    for (const h of heights) expect(h).toBeGreaterThanOrEqual(48)
    await sheet.getByRole('button', { name: 'Close' }).tap()
    await expect(sheet).toHaveCount(0)
  })
})

test('a pending bulk review batch does not lock the checks on a revisit', async ({ page }) => {
  await page.route('**/api/jobs/bulk_flag_3', (route) =>
    route.fulfill({ json: { ...job('running'), job_id: 'bulk_flag_3', updated_at: Date.now() / 1000 } }))
  await open(page)
  await expect(page.getByRole('button', { name: 'Flag lines for a second look' })).toBeEnabled()
  await expect(page.getByTestId('job-status')).toHaveCount(0)
})

test('a review check left running is shown again, with the checks off', async ({ page }) => {
  await page.route('**/api/jobs/flag_3', (route) =>
    route.fulfill({ json: { ...job('running'), job_id: 'flag_3', updated_at: Date.now() / 1000 } }))
  await open(page)
  await expect(page.getByTestId('job-status')).toContainText('Running')
  await expect(page.getByRole('button', { name: 'Flag lines for a second look' })).toBeDisabled()
  await expect(page.getByText('A review job is running.')).toBeVisible()
})

test('Compact rows is remembered for the next visit', async ({ page }) => {
  await open(page)
  const more = page.getByRole('button', { name: 'More', exact: true })
  if (await more.isVisible()) await more.click()
  await page.getByRole('button', { name: 'Compact rows' }).click()
  await expect(page.locator('.review-lines.is-compact')).toHaveCount(1)
  await page.reload()
  await expect(rows(page)).toHaveCount(3)
  await expect(page.locator('.review-lines.is-compact')).toHaveCount(1)
})

test('flag lines for review runs only on click and reports each result', async ({ page }) => {
  const posts: string[] = []
  await page.route('**/api/export/dramas/3/flag-*', (route) => {
    const name = route.request().url().split('/').pop() ?? ''
    posts.push(name)
    return route.fulfill({
      json: name === 'flag-auto-qc' ? { flagged: 1, cleared: 0, already_flagged: 0, checked: 3 } : { flagged_count: 0 },
    })
  })
  await open(page)
  expect(posts).toEqual([])
  const flags = page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: /^Flag lines for review$/ }) })
  await flags.locator(':scope > summary').click()
  await flags.getByRole('button', { name: 'Flag overlapping lines' }).click()
  await expect(flags.getByTestId('flag-result-overlaps')).toHaveText('Flagged 0 lines.')
  await flags.getByRole('button', { name: 'Run auto-QC and flag' }).click()
  await expect(flags.getByTestId('flag-result-qc')).toContainText('Checked 3 lines: flagged 1')
  expect(posts).toEqual(['flag-overlaps', 'flag-auto-qc'])
})
