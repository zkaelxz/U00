import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Parity R05: a search hit on another page of the list opens on its own page,
// active and briefly highlighted. Drama 3 gets 50 lines (two pages of 40)
// written straight into the throwaway library the test server uses.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5,
                       zh=('独一无二' if i == 44 else f'第{i}句'), en=f'Line {i}') for i in range(50)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

test('a search hit on page 2 opens on its page, active and highlighted', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const rows = page.locator('.review-line:not(.review-skeleton)')
  await expect(rows).toHaveCount(40)

  await page.getByRole('searchbox', { name: 'Search lines' }).or(page.getByRole('textbox', { name: 'Search lines' })).fill('独一无二')
  await expect(rows).toHaveCount(1)
  const show = page.getByRole('button', { name: 'Show on its page: line 45' })
  await expect(show).toBeVisible()
  await show.click()

  // The search is cleared and page 2 (lines 41-50) is shown with #45 active.
  await expect(rows).toHaveCount(10)
  await expect(page.getByText('Page 2 of 2').first()).toBeAttached()
  const hit = page.locator('.review-line[aria-current="true"]')
  await expect(hit).toContainText('#45')
  await expect(hit).toHaveClass(/is-jumped/)
  await expect(hit).toBeFocused()
  await expect(page.getByRole('searchbox', { name: 'Search lines' }).or(page.getByRole('textbox', { name: 'Search lines' }))).toHaveValue('')
  // The highlight fades; the line stays the active one.
  await expect(hit).not.toHaveClass(/is-jumped/, { timeout: 8000 })
  await expect(hit).toHaveAttribute('aria-current', 'true')
})

test('no "Show on its page" outside a search', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
  await expect(page.getByRole('button', { name: /on its page/ })).toHaveCount(0)
})

test('an unsaved edit in the search results is saved before the jump', async ({ page }) => {
  await page.goto('/#/drama/3/review')
  const rows = page.locator('.review-line:not(.review-skeleton)')
  await expect(rows).toHaveCount(40)
  await page.getByPlaceholder('Search source or translation').fill('独一无二')
  await expect(rows).toHaveCount(1)
  await rows.first().getByTestId('line-en').click()
  await rows.first().getByLabel('Translation').fill('One of a kind')
  await page.getByRole('button', { name: 'Show on its page: line 45' }).click()
  const hit = page.locator('.review-line[aria-current="true"]')
  await expect(hit).toContainText('#45')
  await expect(hit).toContainText('One of a kind')
  await expect(rows).toHaveCount(10)
})
