import { expect, test } from '@playwright/test'

import { TRACKED, mockAccess, openProxy } from './sourcesAccessMocks'
import { mockSources, posted } from './sourcesMocks'

// Desktop: Check now and the tracked series' auto-import drama (New
// chapters), sign-in and per-tier tests (a source's Details) and the proxy
// (Source settings). Every call is mocked (sourcesMocks.ts, sourcesAccessMocks.ts).

test('check now runs, reports, and reloads the lists; auto-import drama is saved', async ({ page }) => {
  const s = await mockSources(page, { tracked: TRACKED })
  await mockAccess(page, s)
  await page.goto('/#/sources')
  const news = page.getByRole('region', { name: 'New chapters' })
  await news.getByRole('button', { name: 'Check now' }).click()
  await expect(news.getByText('Checked 1 series · 1 new chapter.')).toBeVisible({ timeout: 15_000 })
  expect(posted(s, '/api/sources/check-now')).toHaveLength(1)
  // The lists are fetched again once the check is done.
  await expect.poll(() => s.calls.filter((c) => c.method === 'GET' && c.path.startsWith('/api/sources/notifications')).length).toBeGreaterThan(1)

  // beta is a novel source: only novel dramas are offered.
  const select = news.getByRole('combobox', { name: 'Auto-import into' })
  await expect(select.locator('option')).toHaveText(['None', 'Heaven Novel'])
  await select.selectOption({ label: 'Heaven Novel' })
  await expect.poll(() => posted(s, '/api/sources/tracked/drama').length).toBe(1)
  expect(posted(s, '/api/sources/tracked/drama')[0].body).toEqual({ source: 'beta', series_id: 'b0', drama_id: 11 })
  await expect(select).toHaveValue('11')
  expect(s.unmocked).toEqual([])
})

test('details: test one tier, sign in, forget the sign-in', async ({ page }) => {
  const s = await mockSources(page)
  await mockAccess(page, s)
  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').first().click()
  const row = settings.getByRole('row', { name: /Beta Novels/ })
  await row.getByRole('button', { name: 'Details' }).click()
  const access = settings.getByRole('group', { name: 'Access tests and sign-in: Beta Novels' })

  // No page yet: the tests wait for one.
  await expect(access.getByRole('button', { name: 'Static' })).toBeDisabled()
  await access.getByRole('textbox', { name: 'Page on this site' }).fill('https://beta.example/book/1')
  await access.getByRole('button', { name: 'Static' }).click()
  await expect(access.getByText('Static: works.')).toBeVisible({ timeout: 15_000 })
  expect(posted(s, '/api/sources/beta/tier-test')[0].body).toEqual({ tier: 'static', url: 'https://beta.example/book/1' })

  await access.getByRole('button', { name: 'Open sign-in window' }).click()
  await expect(access.getByText('Signed in -- this page is visible.')).toBeVisible({ timeout: 15_000 })
  expect(posted(s, '/api/sources/beta/signin/open')[0].body).toEqual({ url: 'https://beta.example/book/1' })

  await access.getByRole('button', { name: 'Forget Beta Novels sign-in' }).click()
  await access.getByRole('button', { name: 'Confirm forget Beta Novels sign-in' }).click()
  await expect(row.getByText('No sign-in')).toBeVisible()
  expect(posted(s, '/api/sources/beta/signin/forget')[0].body).toEqual({ confirm: true })
  // PC-only calls carry the local header.
  expect(s.unmocked).toEqual([])
})

test('proxy: saved write-only, then cleared', async ({ page }) => {
  const s = await mockSources(page)
  await mockAccess(page, s)
  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').first().click()
  await openProxy(page)
  const proxy = settings.getByTestId('sources-proxy')
  const box = proxy.getByRole('textbox', { name: 'Proxy (none)' })
  await box.fill('socks5://127.0.0.1:1080')
  await expect(proxy.getByRole('button', { name: 'Save proxy' })).toBeDisabled()
  await box.fill('http://user:pw@127.0.0.1:8080')
  await proxy.getByRole('button', { name: 'Save proxy' }).click()
  await expect(proxy.getByText('Proxy saved.')).toBeVisible()
  await expect(proxy.getByRole('textbox', { name: 'Proxy (set)' })).toHaveValue('')
  await proxy.getByRole('button', { name: 'Clear proxy' }).click()
  await expect(proxy.getByText('Proxy cleared.')).toBeVisible()
  expect(posted(s, '/api/sources/settings/proxy').map((c) => c.body)).toEqual([
    { url: 'http://user:pw@127.0.0.1:8080' },
    { url: '' },
  ])
  expect(s.unmocked).toEqual([])
})

test('proxy section: closed by default showing none, open state remembered', async ({ page }) => {
  const s = await mockSources(page)
  await mockAccess(page, s)
  await page.goto('/#/sources')
  const settings = page.getByRole('region', { name: 'Source settings' })
  await settings.getByText('Source settings').first().click()
  const details = page.locator('details.section:has(> summary > .section-title:text-is("Proxy"))')
  await expect(details).not.toHaveAttribute('open', '')
  await expect(details.locator('summary')).toContainText(/none/i)
  await details.locator('summary').click()
  await expect(details).toHaveAttribute('open', '')
  await page.waitForFunction(() => localStorage.getItem('baihe.section.sources.proxy') === '1')
  await page.reload()
  await settings.getByText('Source settings').first().click()
  await expect(details).toHaveAttribute('open', '')
  expect(s.unmocked).toEqual([])
})

test('another device: no Check now, no drama link, settings PC only', async ({ page }) => {
  const s = await mockSources(page, { tracked: TRACKED, local: false })
  await mockAccess(page, s)
  await page.goto('/#/sources')
  const news = page.getByRole('region', { name: 'New chapters' })
  await expect(news.getByText('Heaven Book 1 · Beta Novels')).toBeVisible()
  await expect(news.getByRole('button', { name: 'Check now' })).toHaveCount(0)
  await expect(news.getByRole('combobox', { name: 'Auto-import into' })).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Source settings' }).getByText('PC only')).toBeVisible()
  expect(s.calls.some((c) => c.path.includes('sources_chapter_check'))).toBe(false)
  expect(s.unmocked).toEqual([])
})
