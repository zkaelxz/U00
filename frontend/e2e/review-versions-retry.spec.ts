import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

// Review parity R39 (use a saved translation version) and R10 (retry a
// content-blocked line). Both run against the real seeded API: the version
// switch is a real field-scoped write, and the retry uses the free
// fake engine (no network, no key). Lines and versions for drama 3
// are written straight into the throwaway library before each test and the
// versions are removed after, so review-stage.spec.ts sees none.

test.use({ viewport: { width: 1280, height: 800 } })

const SHOTS = process.env.SHOT_DIR
const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

test.beforeEach(() => {
  python(`
from core import Line
db.update_drama(3, audio_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=1, start=1.5, end=3.0, zh='再见朋友', en='', flag='content_blocked', flag_note='claude: refusal'),
    Line(idx=2, start=3.0, end=4.5, zh='谢谢', en='Thanks, friend'),
])
lines = db.load_line_objects(3)
for ln, en in zip(lines, ['Earlier hello', 'Earlier bye', 'Earlier thanks']):
    ln.en = en
db.save_translation_version(3, lines, 'Earlier pass', 'claude', 'm')
`)
})

test.afterEach(() => {
  python(`
for v in db.list_translation_versions(3):
    db.delete_translation_version(v['id'])
`)
})

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

async function openSection(page: Page, title: string) {
  await page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }).first().click()
}

test('Use this version reports lines edited meanwhile as kept', async ({ page }) => {
  await page.route('**/api/review/dramas/3/versions/*/activate', (route) =>
    route.fulfill({ json: { drama_id: 3, version_id: 1, label: 'Earlier pass', activated: true,
                            lines_changed: 1, conflicts: [11, 12] } }))
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(3)
  await openSection(page, 'Records')
  const list = page.getByTestId('versions-list')
  await list.getByRole('button', { name: 'Use this version Earlier pass', exact: true }).click()
  await list.getByRole('button', { name: 'Confirm: replace the English with Earlier pass' }).click()
  await expect(page.getByTestId('activate-status')).toContainText('2 lines were edited meanwhile and kept.')
})

test('Use this version: two steps, then the English is replaced and the list reloads', async ({ page }) => {
  let calls = 0
  page.on('request', (r) => {
    if (r.method() === 'POST' && r.url().includes('/activate')) calls += 1
  })
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(3)
  await openSection(page, 'Records')
  const list = page.getByTestId('versions-list')
  await list.getByRole('button', { name: 'Use this version Earlier pass', exact: true }).click()
  await expect(list.getByRole('button', { name: 'Confirm: replace the English with Earlier pass' })).toBeVisible()
  expect(calls).toBe(0)
  await expect(list).toContainText('Press again to use Earlier pass')
  await shot(page, 'records-use-version-armed-desktop')
  await list.getByRole('button', { name: 'Confirm: replace the English with Earlier pass' }).click()
  await expect(page.getByTestId('activate-status')).toContainText('Now using “Earlier pass” (3 lines changed)')
  expect(calls).toBe(1)
  await expect(rows(page).nth(0).getByTestId('line-en')).toContainText('Earlier hello')
  await expect(rows(page).nth(2).getByTestId('line-en')).toContainText('Earlier thanks')
  // Now active: no "Use this version" for it any more.
  await expect(list).toContainText('active')
  await expect(list.getByRole('button', { name: 'Use this version Earlier pass', exact: true })).toHaveCount(0)
  await shot(page, 'records-use-version-done-desktop')
})

test('Retry a content-blocked line with another engine from the line details', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(3)
  const row = rows(page).nth(1)
  await row.locator('.review-idx').click()
  await expect(row).toHaveAttribute('aria-current', 'true')
  await expect(row.getByTestId('blocked-retry')).toHaveCount(0)
  await row.getByRole('button', { name: 'Edit details' }).click()
  const retry = row.getByTestId('blocked-retry')
  await expect(retry).toBeVisible()
  const picker = retry.getByLabel('Retry engine')
  await expect(picker).toHaveValue('ollama')
  await picker.selectOption('fake')
  await shot(page, 'review-blocked-retry-desktop')
  await retry.getByRole('button', { name: 'Retry line' }).click()
  await expect(row.getByTestId('line-en')).toContainText('[TEST]')
  await expect(row).not.toHaveAttribute('data-flagged', 'true')
  await expect(row.getByTestId('blocked-retry')).toHaveCount(0)
})

test('Retry is off while the details draft has unsaved changes', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(3)
  const row = rows(page).nth(1)
  await row.locator('.review-idx').click()
  await row.getByRole('button', { name: 'Edit details' }).click()
  const retry = row.getByTestId('blocked-retry')
  await expect(retry.getByRole('button', { name: 'Retry line' })).toBeEnabled()
  await row.getByLabel('Translation').fill('my own draft')
  await expect(retry.getByRole('button', { name: 'Retry line' })).toBeDisabled()
  await expect(retry.getByTestId('retry-reason')).toHaveText('Save or discard your edit first.')
})

test('A second block keeps the flag, updates its note in place and shows the reason', async ({ page }) => {
  await page.route('**/api/lines/dramas/3/lines/*/retry-blocked', (route) => {
    const id = Number(route.request().url().split('/lines/')[2].split('/')[0])
    // What the real route would have stored, so the reload after it agrees.
    python(`db.update_line_fields_if(3, ${id}, {'flag_note': 'gemini: SAFETY'}, {})`)
    return route.fulfill({
      json: {
        drama_id: 3, line_id: id, engine: 'gemini', model: null, retried: false, blocked: true, reason: 'SAFETY',
        line: {
          id, idx: 1, start: 1.5, end: 3.0, zh: '再见朋友', en: '', speaker: null, speaker_manual: false, sfx: false,
          flag: 'content_blocked', flag_note: 'gemini: SAFETY', dub_filename: null,
        },
      },
    })
  })
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(3)
  const row = rows(page).nth(1)
  await row.locator('.review-idx').click()
  await row.getByRole('button', { name: 'Edit details' }).click()
  const retry = row.getByTestId('blocked-retry')
  await retry.getByRole('button', { name: 'Retry line' }).click()
  await expect(retry.getByTestId('blocked-again')).toHaveText('Gemini also blocked this line: SAFETY')
  await expect(row).toHaveAttribute('data-flagged', 'true')
  await expect(row.getByTestId('line-flag')).toContainText('gemini: SAFETY')
})
