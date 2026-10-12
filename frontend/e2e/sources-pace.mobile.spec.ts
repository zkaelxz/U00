import { expect, test } from '@playwright/test'

import { installHitArea } from './hitArea'
import { SETTINGS, SOURCES, mockSources, openManage } from './sourcesMocks'

// Manage sources > Sites > Rules (phone, 390x844 touch). Writes are mocked (sourcesMocks.ts).

test.beforeEach(async ({ page }) => {
  await installHitArea(page)
})

test('phone rules: Fast is disabled and says so where unvetted, a change posts, a refusal rolls back', async ({ page }) => {
  const s = await mockSources(page)
  await page.route(/\/api\/sources\/settings$/, (route) =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(SETTINGS) }))
  let status = 200
  await page.route(/\/api\/sources\/(alpha|beta)\/pace$/, (route) => {
    const body = route.request().postDataJSON()
    s.calls.push({ method: 'POST', path: new URL(route.request().url()).pathname, body })
    if (status !== 200) {
      return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ error: { code: 'validation_error', message: 'nope' } }) })
    }
    const name = route.request().url().includes('/alpha/') ? 'alpha' : 'beta'
    const row = SOURCES.find((x) => x.name === name)
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ...row, pace: body.pace }) })
  })

  const settings = await openManage(page)

  const alpha = settings.getByRole('combobox', { name: 'Rules: Alpha Comics' })
  await expect(alpha).toHaveValue('normal')
  await expect(alpha.locator('option')).toHaveText(['Gentle', 'Standard', 'Fast (not available)'])
  await expect(alpha.locator('option[value="fast"]')).toBeDisabled()
  await expect(settings.getByText("Fast is only available for sites whose rules we've checked.")).toHaveCount(1)
  await expect(settings.locator('.source-cards li', { hasText: 'Alpha Comics' })).not.toContainText('Fast is')
  const beta = settings.getByRole('combobox', { name: 'Rules: Beta Novels' })
  await expect(beta.locator('option[value="fast"]')).toBeEnabled()

  for (const sel of [alpha, beta]) expect(await sel.evaluate((e) => window.hitHeight(e as HTMLElement))).toBeGreaterThanOrEqual(44)
  const { scroll, client } = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth }))
  expect(scroll, 'page scrolls sideways').toBeLessThanOrEqual(client)

  await alpha.selectOption('careful')
  await expect(alpha).toHaveValue('careful')
  expect(s.calls.filter((c) => c.path === '/api/sources/alpha/pace').map((c) => c.body)).toEqual([{ pace: 'careful' }])

  status = 422
  await beta.selectOption('fast')
  await expect(settings.getByRole('alert')).toBeVisible()
  await expect(beta).toHaveValue('normal')
})
