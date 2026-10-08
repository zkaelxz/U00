import { expect, test } from '@playwright/test'

import { mockExtensionOnly } from './sourcesExtensionMocks'
import { mockSources, posted } from './sourcesMocks'

// Desktop: a source's Details carry the "works only with the browser extension"
// marker. Every call is mocked (sourcesMocks.ts, sourcesExtensionMocks.ts).

async function openAlpha(page: import('@playwright/test').Page) {
  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').first().click()
  await settings.getByRole('row', { name: /Alpha Comics/ }).getByRole('button', { name: 'Details' }).click()
  return settings
}

test('marking a source shows Extension only and keeps the failed tests', async ({ page }) => {
  const s = await mockSources(page)
  await mockExtensionOnly(page, s)
  const settings = await openAlpha(page)
  const dl = settings.locator('.source-dl')
  await expect(dl).toContainText('Untested')
  await expect(dl).toContainText('You in a browser: untested')

  await settings.getByRole('switch', { name: 'Works only with the browser extension' }).click()
  await expect(dl).toContainText('Extension only')
  await expect(settings.getByText('Marked by you, 2026-10-08.')).toBeVisible()
  await expect(dl).toContainText('Browser extension')
  await expect(dl).toContainText('You in a browser: works (marked by you, 2026-10-08)')
  // The automated tiers stay as tested.
  await expect(dl).toContainText('Static: failed (empty spa shell)')
  await expect(dl).toContainText('Browser: failed (javascript required)')
  await expect(settings.getByRole('row', { name: /Alpha Comics/ }).locator('.pill', { hasText: 'Extension only' })).toBeVisible()
  expect(posted(s, '/api/sources/alpha/extension-only')[0].body).toEqual({ extension_only: true, note: '' })

  await settings.getByRole('textbox', { name: 'Note' }).fill('Needs Chrome')
  await settings.getByRole('button', { name: 'Save note' }).click()
  await expect.poll(() => posted(s, '/api/sources/alpha/extension-only').length).toBe(2)
  expect(posted(s, '/api/sources/alpha/extension-only')[1].body).toEqual({ extension_only: true, note: 'Needs Chrome' })

  await settings.getByRole('switch', { name: 'Works only with the browser extension' }).click()
  await expect(dl).toContainText('Untested')
  expect(s.unmocked).toEqual([])
})

test('a later passing test shows the hint and does not clear the marker', async ({ page }) => {
  const s = await mockSources(page)
  await mockExtensionOnly(page, s, { on: true, worksWithout: true })
  const settings = await openAlpha(page)
  await expect(settings.getByText('This now works without the extension: clear the marker?')).toBeVisible()
  await expect(settings.locator('.source-dl')).not.toContainText('Extension only')
  expect(posted(s, '/api/sources/alpha/extension-only')).toHaveLength(0)

  await settings.getByRole('button', { name: 'Clear marker' }).click()
  await expect(settings.getByText('This now works without the extension')).toHaveCount(0)
  expect(posted(s, '/api/sources/alpha/extension-only')[0].body).toEqual({ extension_only: false })
})

test('a tracked series of a marked source reads "extension only: skipped"', async ({ page }) => {
  const tracked = [{ source: 'alpha', series_id: 'a0', title: 'Heaven Book 1', url: 'https://alpha.example/a',
    drama_id: null, last_checked: null, last_check_error: null, extension_only: true }]
  const s = await mockSources(page, { tracked })
  await mockExtensionOnly(page, s, { on: true })
  await page.goto('/#/sources')
  const news = page.getByRole('region', { name: 'New chapters' })
  await expect(news.getByText('extension only: skipped')).toBeVisible()
  await expect(news.getByText('not checked yet')).toHaveCount(0)
})
