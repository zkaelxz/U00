import { execFileSync } from 'node:child_process'
import path from 'node:path'

import { expect, test } from '@playwright/test'

// Failed requests say the real reason: a 422 shows the server's own sentence,
// a 429 its wait text, a failed read says "Could not load" with a Retry, and a
// Reader page that fails to load leaves the page indicator and URL on the last
// good page. The Reader runs against the real API (90 lines written into the
// throwaway library for drama 2, as reader.spec.ts does); only the failures
// are mocked.

const repoRoot = path.resolve(process.cwd(), '..')
const libraryDir = path.join(repoRoot, 'frontend', 'test-results', 'e2e-library')

test.beforeEach(() => {
  const code = `
import contextlib
import db
db.configure_library_dir(${JSON.stringify(libraryDir)})
from core import Line
db.save_lines(2, [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh='第%d句话' % i, en='Line %d' % i) for i in range(90)])
with contextlib.closing(db.get_conn()) as c:
    c.execute('DELETE FROM progress WHERE drama_id = 2')
    c.commit()
`
  execFileSync(process.env.PYTHON ?? 'python', ['-c', code], { cwd: repoRoot })
})

const label = (page: import('@playwright/test').Page) => page.getByTestId('reader-page-label').first()
const fail = (status: number, code: string, message: string) => ({ status, json: { error: { code, message } } })

for (const [status, code, message] of [
  [500, 'internal_error', 'boom'],
  [429, 'rate_limited', 'Wait a few seconds before loading again.'],
] as const) {
  test(`a ${status} on the next page keeps the last good page and offers Retry`, async ({ page }) => {
    await page.goto('/#/read/2?page=1')
    await expect(label(page)).toHaveText('Page 1 of 3')
    let failing = true
    await page.route('**/api/reader/dramas/2/page?*', (route) => {
      const wanted = new URL(route.request().url()).searchParams.get('page')
      return failing && wanted === '2' ? route.fulfill(fail(status, code, message)) : route.fallback()
    })
    await page.getByRole('button', { name: 'Next page' }).first().click()
    const alert = page.getByRole('alert').filter({ hasText: /Could not load|Wait a few seconds/ })
    await expect(alert).toBeVisible()
    if (status === 429) await expect(alert).toContainText(message)
    else await expect(alert).toContainText('Could not load')
    await expect(alert).not.toContainText('Nothing was changed')
    await expect(page).toHaveURL(/#\/read\/2\?page=1$/)
    await expect(label(page)).toHaveText('Page 1 of 3')

    failing = false
    await alert.getByRole('button', { name: 'Retry' }).click()
    await expect(page).toHaveURL(/page=2$/)
    await expect(label(page)).toHaveText('Page 2 of 3')
    await expect(page.getByRole('alert').filter({ hasText: 'Could not load' })).toHaveCount(0)
  })
}

test('a title that fails to load shows the error with Retry instead of Loading forever', async ({ page }) => {
  let failing = true
  await page.route('**/api/library/dramas/2', (route) =>
    failing ? route.fulfill(fail(500, 'internal_error', 'boom')) : route.fallback(),
  )
  await page.goto('/#/read/2?page=1')
  const alert = page.getByRole('alert').filter({ hasText: 'Could not load' })
  await expect(alert).toBeVisible()
  failing = false
  await alert.getByRole('button', { name: 'Retry' }).click()
  await expect(alert).toHaveCount(0)
})

test('a form shows the server sentence for a 422 and never the generic text', async ({ page }) => {
  await page.route('**/api/reader/dramas/2/notes', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill(fail(422, 'validation_error', '2000 characters max'))
      : route.fallback(),
  )
  await page.goto('/#/read/2?page=1')
  await page.locator('summary', { hasText: 'My notes' }).click()
  await page.getByRole('textbox', { name: 'Notes' }).fill('x')
  await page.getByRole('button', { name: 'Save' }).click()
  const alert = page.getByRole('alert').filter({ hasText: '2000 characters max' })
  await expect(alert).toBeVisible()
  await expect(page.getByText('Some of the values entered are not valid')).toHaveCount(0)
})

test('a form shows the 429 wait text and a 500 as plain text', async ({ page }) => {
  let status = 429
  await page.route('**/api/reader/dramas/2/notes', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill(status === 429
        ? fail(429, 'rate_limited', 'Wait a few seconds before saving again.')
        : fail(500, 'internal_error', 'Traceback /home/x/y.py'))
      : route.fallback(),
  )
  await page.goto('/#/read/2?page=1')
  await page.locator('summary', { hasText: 'My notes' }).click()
  await page.getByRole('textbox', { name: 'Notes' }).fill('x')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByRole('alert').filter({ hasText: /AI is busy|Wait a few seconds/ })).toBeVisible()
  status = 500
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'Something went wrong inside Baihe' })).toBeVisible()
  await expect(page.getByText('/home/x/y.py')).toHaveCount(0)
})
