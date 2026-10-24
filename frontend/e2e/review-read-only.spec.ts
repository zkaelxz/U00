import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

import { ME } from './authMocks'
import { clearDrama3Lines } from './reviewResultsSeed'

// Review for a household member an admin took "Edit lines" away from (library.read, lines.read and
// review.use only). Reads go to the seeded API (auth off); /api/auth/me and /api/meta are mocked and every
// write is answered 403 here, as the real server answers this member.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')
function python(code: string) {
  execFileSync(process.env.PYTHON ?? 'python', ['-c', `import db\ndb.configure_library_dir(${JSON.stringify(libraryDir)})\n${code}`], { cwd: repoRoot })
}
test.beforeEach(() => python(`
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [
    Line(idx=0, start=0.0, end=1.5, zh='你好', en='Hello there'),
    Line(idx=1, start=1.5, end=3.0, zh='谢谢', en='Thanks, friend'),
])
`))
test.afterEach(() => clearDrama3Lines())

const rows = (page: Page) => page.locator('.review-line:not(.review-skeleton)')

const REFUSAL = 'Not allowed from this device or account.'

async function signInWithoutLinesEdit(page: Page) {
  const refused: string[] = []
  await page.route('**/api/**', (route) => {
    const req = route.request()
    const p = new URL(req.url()).pathname
    if (p === '/api/auth/me') return route.fulfill({ json: { ...ME.signedIn, permissions: ['library.read', 'lines.read', 'review.use'] } })
    if (p === '/api/meta') return route.fulfill({ json: { app: 'Baihe Studio', api_version: '0.1', environment: 'production', local: false } })
    if (req.method() !== 'GET' && req.method() !== 'HEAD') {
      refused.push(`${req.method()} ${p}`)
      return route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } })
    }
    return route.fallback()
  })
  return refused
}

test('a refused line save says so plainly and keeps the typing', async ({ page }) => {
  const refused = await signInWithoutLinesEdit(page)
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(2)
  const row = rows(page).nth(0)
  await row.getByTestId('line-en').click()
  await row.getByLabel('Translation').fill('Hello, member')
  await row.getByRole('button', { name: 'Save', exact: true }).click()
  await expect(row.getByRole('alert')).toContainText(REFUSAL)
  await expect(row.getByLabel('Translation')).toHaveValue('Hello, member')
  expect(refused).toEqual([expect.stringMatching(/^POST \/api\/lines\/dramas\/3\/lines\/\d+$/)])
})

test('a refused merge says so plainly in the dialog and the lines stay as they were', async ({ page }) => {
  const refused = await signInWithoutLinesEdit(page)
  await page.goto('/#/drama/3/review')
  await expect(rows(page)).toHaveCount(2)
  const row = rows(page).nth(0)
  await row.locator('.review-idx').click()
  await expect(row).toHaveAttribute('aria-current', 'true')
  await page.keyboard.press('m')
  const dialog = page.getByRole('dialog', { name: /Merge #\d+ with next/ })
  await dialog.getByRole('button', { name: /^Merge #/ }).click()
  await expect(dialog.getByRole('alert')).toContainText(REFUSAL)
  await expect(rows(page)).toHaveCount(2)
  expect(refused).toEqual(['POST /api/restructure/dramas/3/merge'])
})
