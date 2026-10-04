import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'
import { openFoldFor } from './reviewFolds'

import { clearReviewResults } from './reviewResultsSeed'

// Review per-line tools and navigation: flagged lines across pages (R08),
// alternatives (R17), grammar (R18), pronounce (R19), translation-memory
// accept/dismiss per line (R11), note -> line (R43), restore the original
// transcript text (R24) and auto-shorten (R28). Drama 3 gets 45 lines, so the
// second flagged line is on page 2. Navigation and line edits use the real
// API; AI, audio and the TM/notes/original-text reads are mocked (they would
// need a series, stored notes or a raw transcript file in the shared library).

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
const shotDir = path.join(repoRoot, 'frontend', 'test-results', 'review-line-tools')

function python(code: string): string {
  return execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot }).toString()
}

const LONG = 'this translation has far too many words to be said in a second and a half of time at all'
let ids: number[] = []

test.beforeEach(() => {
  const out = python(`
import json
from core import Line
db.update_drama(3, audio_filename=None)
lines = [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'句子{i}', en=f'Line {i}') for i in range(45)]
lines[1].flag, lines[1].flag_note = 'uncertain', 'check'
lines[43].flag, lines[43].flag_note = 'uncertain', 'far away'
lines[3].en = ${JSON.stringify(LONG)}
db.save_lines(3, lines)
print(json.dumps([r['id'] for r in db.load_lines(3)]))
`)
  ids = JSON.parse(out.trim().split('\n').pop() as string)
})

test.afterAll(() => {
  clearReviewResults()
  python('db.save_lines(3, [])')
})

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')
const row = (page: Page, idx: number) => page.locator(`.review-line[data-line-id="${ids[idx]}"]`)

async function open(page: Page) {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(40)
}

async function sheetItem(page: Page, idx: number, item: string) {
  const r = row(page, idx)
  await r.locator('.review-idx').click()
  await expect(r).toHaveAttribute('aria-current', 'true')
  await r.getByRole('button', { name: `More actions for line ${idx + 1}` }).click()
  await page.getByRole('button', { name: item }).click()
  return r
}

const section = (page: Page, title: string) =>
  page.locator('details.section').filter({ has: page.locator(':scope > summary .section-title', { hasText: new RegExp(`^${title}$`) }) })

async function openSection(page: Page, title: string) {
  await openFoldFor(page, title)
  const s = section(page, title)
  if ((await s.getAttribute('open')) === null) await s.locator(':scope > summary').click()
  return s
}

test('next/previous flagged cross pages; Alt+Down too', async ({ page }) => {
  await open(page)
  await expect(row(page, 1)).toHaveAttribute('aria-current', 'true')
  await page.getByRole('button', { name: 'Next flagged ›' }).click()
  await expect(row(page, 43)).toBeFocused()
  await expect(row(page, 43)).toHaveAttribute('aria-current', 'true')
  await expect(rows(page)).toHaveCount(5)
  await page.screenshot({ path: `${shotDir}/desktop-flagged-page2.png` })

  await page.getByRole('button', { name: 'Next flagged ›' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'No more flagged lines.' })).toBeVisible()

  await page.getByRole('button', { name: '‹ Previous flagged' }).click()
  await expect(row(page, 1)).toBeFocused()
  await page.keyboard.press('Alt+ArrowDown')
  await expect(row(page, 43)).toBeFocused()
})

test('alternatives: use one, saved through the line patch', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/alternatives', (route) =>
    route.fulfill({ json: {
      line_id: ids[0], current_en: 'Line 0', engine: 'x', model: null,
      alternatives: [
        { translation: 'First line', approach: 'more natural', tradeoff: 'literalness' },
        { translation: 'Line zero', approach: 'literal', tradeoff: 'flow' },
      ],
    } }))
  await open(page)
  const r = await sheetItem(page, 0, 'Alternatives (AI)')
  const list = r.getByTestId('line-alternatives')
  await expect(list).toContainText('trades away: literalness')
  await page.screenshot({ path: `${shotDir}/desktop-alternatives.png` })
  await list.getByRole('button', { name: 'Use this' }).first().click()
  await expect(r.getByTestId('line-en')).toHaveText('First line')
  await expect(page.getByTestId('line-tools-panel')).toHaveCount(0)
  expect(python(`print([r['en'] for r in db.load_lines(3)][0])`).trim()).toBe('First line')
})

test('grammar lists the parts; pronounce plays a clip', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/grammar', (route) =>
    route.fulfill({ json: {
      line_id: ids[2], zh: '句子2', engine: 'x', model: null,
      parts: [
        { word: '句子', reading: 'jùzi', meaning: 'sentence', function: 'noun' },
        { word: '2', reading: 'èr', meaning: 'two', function: 'numeral' },
      ],
    } }))
  let pronounced = 0
  await page.route('**/api/line-ai/dramas/3/lines/*/pronounce', (route) => {
    pronounced += 1
    expect(route.request().method()).toBe('POST')
    return route.fulfill({ status: 200, contentType: 'audio/mpeg', body: Buffer.from('ID3fake') })
  })
  await open(page)
  const r = await sheetItem(page, 2, 'Grammar breakdown (AI)')
  await expect(r.getByTestId('line-grammar')).toContainText('jùzi')
  await expect(r.getByTestId('line-grammar').locator('li')).toHaveCount(2)
  await r.getByRole('button', { name: 'Hide grammar' }).click()

  await sheetItem(page, 2, 'Pronounce the source')
  await expect(r.getByTestId('pronounce-audio')).toHaveAttribute('src', /^blob:/)
  expect(pronounced).toBe(1)
})

test('an AI tool without a key points to Settings', async ({ page }) => {
  await page.route('**/api/line-ai/dramas/3/lines/*/alternatives', (route) =>
    route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: 'no key' } } }))
  await open(page)
  const r = await sheetItem(page, 0, 'Alternatives (AI)')
  await expect(r.getByTestId('line-tools-unavailable')).toContainText('Settings')
})

test('translation memory: dismiss one on its line, use another', async ({ page }) => {
  const tm = (idx: number, suggestion: string, entry: number) => ({
    line_id: ids[idx], line_idx: idx, zh: `句子${idx}`, en: `Line ${idx}`, suggestion, similarity: 0.93, exact: false, entry_id: entry,
  })
  await page.route('**/api/review/dramas/3/tm-suggestions*', (route) =>
    route.fulfill({ json: [tm(0, 'Remembered zero', 11), tm(2, 'Remembered two', 12)] }))
  await page.route('**/api/lines/dramas/3/lines/*/accept-tm', (route) => {
    expect(route.request().postDataJSON()).toEqual({ entry_id: 12, expected_en: 'Line 2' })
    // Save it for real: the panel reloads its lines after a save, and a reload
    // that still read the old text would race the assertion below.
    python(`db.update_line_fields_if(3, ${ids[2]}, {'en': 'Remembered two'}, {'en': 'Line 2'})`)
    return route.fulfill({ json: {
      id: ids[2], idx: 2, start: 4, end: 5.5, zh: '句子2', en: 'Remembered two', speaker: null,
      speaker_manual: false, sfx: false, flag: null, flag_note: null, dub_filename: null,
    } })
  })
  await open(page)
  await expect(row(page, 0).getByTestId('line-tm')).toContainText('Remembered zero')
  await expect(row(page, 0).getByTestId('line-tm')).toContainText('93% similar')
  await row(page, 0).getByTestId('line-tm').getByRole('button', { name: 'Dismiss' }).click()
  await expect(row(page, 0).getByTestId('line-tm')).toHaveCount(0)
  // The Records list hides it too.
  const records = await openSection(page, 'Records')
  await expect(records.getByTestId('tm-list')).not.toContainText('Remembered zero')
  await expect(records.getByTestId('tm-list')).toContainText('Remembered two')

  const reloaded = page.waitForResponse((r) => r.request().method() === 'GET' && /\/api\/review\/dramas\/3\/lines\?/.test(r.url()))
  await row(page, 2).getByTestId('line-tm').getByRole('button', { name: 'Use' }).click()
  await reloaded
  await expect(row(page, 2).getByTestId('line-en')).toHaveText('Remembered two')

  // Dismissed stays dismissed after a reload of the page (same tab session).
  await page.reload()
  await expect(rows(page)).toHaveCount(40)
  await expect(row(page, 0).getByTestId('line-tm')).toHaveCount(0)
})

test('a note opens its line, even on another page', async ({ page }) => {
  await page.route('**/api/review/dramas/3/notes', (route) =>
    route.fulfill({ json: [{
      id: 5, drama_id: 3, line_id: ids[43], line_idx: 43, term: '句子', note_type: 'culture', note: 'Far down.', created_at: null,
    }] }))
  await open(page)
  const records = await openSection(page, 'Records')
  await records.getByTestId('notes-list').getByRole('button', { name: 'Go to line' }).click()
  await expect(row(page, 43)).toBeFocused()
  await expect(row(page, 43)).toHaveAttribute('aria-current', 'true')
})

test('restore the original transcript text of one line', async ({ page }) => {
  await page.route('**/api/review/dramas/3/lines/*/original-text', (route) =>
    route.fulfill({ json: {
      line_id: ids[0], idx: 0, current_zh: '句子0', has_raw_transcript: true, original_text: '原来的句子', differs: true,
    } }))
  await open(page)
  const r = row(page, 0)
  await r.locator('.review-idx').click()
  await r.getByRole('button', { name: 'Edit details' }).click()
  await r.locator('summary', { hasText: 'Where this line came from' }).click()
  await expect(r.getByTestId('original-text')).toContainText('原来的句子')
  await r.getByTestId('restore-original').click()
  // Like re-transcribe's "Use this": the row shows the saved text and a clean edit closes.
  await expect(r.locator('.review-zh')).toHaveText('原来的句子')
  await expect(r.getByLabel('Speaker')).toHaveCount(0)
  expect(python(`print([r['zh'] for r in db.load_lines(3)][0])`).trim()).toBe('原来的句子')
})

test('shorten overlong lines asks first, then reports what changed', async ({ page }) => {
  let calls = 0
  let sent: unknown = null
  await page.route('**/api/lines/dramas/3/shorten-overlong', (route) => {
    calls += 1
    sent = route.request().postDataJSON()
    return route.fulfill({ json: {
      shortened: 1, unchanged: 0, stale: 0, remaining: 0, snapshot_saved: true,
      lines: [{ id: ids[3], idx: 3, before: LONG, after: 'Too many words.' }],
    } })
  })
  await open(page)
  const cov = await openSection(page, 'Shorten overlong')
  const box = cov.getByTestId('shorten-overlong')
  await box.getByRole('button', { name: /Shorten overlong lines/ }).click()
  expect(calls).toBe(0)
  await box.getByRole('button', { name: /Confirm: rewrite the English of 1 overlong line/ }).click()
  await expect(box.getByTestId('shorten-status')).toContainText('Shortened 1 line')
  await expect(box.getByTestId('shorten-status')).toContainText('Too many words.')
  expect(calls).toBe(1)
  expect(sent).toEqual({ confirm: true })
  await cov.screenshot({ path: `${shotDir}/desktop-shorten.png` })
})
