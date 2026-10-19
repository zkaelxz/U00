import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test, type Page } from '@playwright/test'

// The Review pager's page number is a box you can type into (4 pages of 40 lines here).

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.update_drama(3, audio_filename=None, source_video_filename=None)
db.save_lines(3, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh=f'第{i}句', en=f'Line {i}') for i in range(130)])
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

const input = (page: Page) => page.getByLabel('Page number').first()

async function open(page: Page) {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/#/drama/3/review')
  await expect(page.locator('.review-line:not(.review-skeleton)')).toHaveCount(40)
}

test('typing a page number and pressing Enter jumps there', async ({ page }) => {
  await open(page)
  await expect(input(page)).toHaveValue('1')
  await input(page).fill('3')
  await input(page).press('Enter')
  await expect(input(page)).toHaveValue('3')
  await expect(page.getByText('Line 80', { exact: true }).first()).toBeVisible()
})

test('a number past the end is clamped when the box loses focus', async ({ page }) => {
  await open(page)
  await input(page).fill('99')
  await page.getByRole('radiogroup', { name: 'Show' }).click({ position: { x: 1, y: 1 } })
  await expect(input(page)).toHaveValue('4')
  await input(page).fill('0')
  await input(page).press('Enter')
  await expect(input(page)).toHaveValue('1')
})

test('Escape and junk text keep the current page; the arrows still work', async ({ page }) => {
  await open(page)
  await input(page).fill('3')
  await input(page).press('Escape')
  await expect(input(page)).toHaveValue('1')
  await input(page).fill('abc')
  await input(page).press('Enter')
  await expect(input(page)).toHaveValue('1')
  await page.getByRole('button', { name: 'Next page' }).first().click()
  await expect(input(page)).toHaveValue('2')
})
