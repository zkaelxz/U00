import { expect, test } from '@playwright/test'

import { SETTINGS, SOURCES, mockSources } from './sourcesMocks'

// Source settings -> Pace (desktop). Writes are mocked (sourcesMocks.ts).

test('pace: Fast is disabled with a reason where unvetted, a change posts, a refusal rolls back', async ({ page }) => {
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

  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').click()

  const alpha = settings.getByRole('combobox', { name: 'Pace: Alpha Comics' })
  await expect(alpha).toHaveValue('normal')
  await expect(alpha.locator('option[value="fast"]')).toBeDisabled()
  await expect(settings.locator('tbody tr', { hasText: 'Alpha Comics' })).toContainText("Fast is off: this site's rules haven't been checked.")
  // A site whose rules were checked offers Fast and needs no explanation.
  const beta = settings.getByRole('combobox', { name: 'Pace: Beta Novels' })
  await expect(beta.locator('option[value="fast"]')).toBeEnabled()
  await expect(settings.locator('tbody tr', { hasText: 'Beta Novels' })).not.toContainText('Fast is off')

  await alpha.selectOption('careful')
  await expect(alpha).toHaveValue('careful')
  expect(s.calls.filter((c) => c.path === '/api/sources/alpha/pace').map((c) => c.body)).toEqual([{ pace: 'careful' }])

  status = 422
  await beta.selectOption('fast')
  await expect(settings.getByRole('alert')).toBeVisible()
  await expect(beta).toHaveValue('normal')
})
