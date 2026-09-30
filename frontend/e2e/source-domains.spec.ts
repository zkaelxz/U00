import { expect, test, type Page } from '@playwright/test'

import { mockDomains, type DomainsState } from './sourceDomainsMocks'
import { mockSources } from './sourcesMocks'

// Settings > Sources > Source addresses: edit, reset, and confirm or dismiss
// a possible new address. Everything is mocked; an older server (404) hides it.

async function open(page: Page, over: Partial<DomainsState> = {}) {
  const s = await mockSources(page)
  const d = await mockDomains(page, over)
  await page.goto('/#/sources')
  await page.getByText('Source settings', { exact: true }).first().click()
  return { s, d }
}

test('edit order and add a host; save sends only the list', async ({ page }) => {
  const { s, d } = await open(page)
  await page.getByText('Source addresses').first().click()
  const beta = page.getByRole('group', { name: 'Beta Novels addresses' })
  await beta.getByRole('textbox', { name: 'Add a host for Beta Novels' }).fill('https://bad.example/x')
  await beta.getByRole('button', { name: 'Add', exact: true }).click()
  await expect(beta.getByRole('alert')).toContainText('without http')
  await beta.getByRole('textbox').fill('Beta2.Example.')
  await beta.getByRole('button', { name: 'Add', exact: true }).click()
  await beta.getByRole('button', { name: 'Move beta2.example up' }).click()
  await beta.getByRole('button', { name: 'Save' }).click()
  await expect(beta.getByText('Customized')).toBeVisible()
  expect(d.calls.at(-1)).toMatchObject({ path: '/api/source-domains/beta', body: { domains: ['beta2.example', 'beta.example'] } })
  await page.screenshot({ path: 'test-results/source-domains-desktop.png', fullPage: true })
  expect(s.unmocked).toEqual([])
})

test('a server 422 is shown as plain text', async ({ page }) => {
  await open(page, { saveError: 'Host is not allowed.' })
  await page.getByText('Source addresses').first().click()
  const beta = page.getByRole('group', { name: 'Beta Novels addresses' })
  await beta.getByRole('textbox').fill('ok.example')
  await beta.getByRole('button', { name: 'Add', exact: true }).click()
  await beta.getByRole('button', { name: 'Save' }).click()
  await expect(beta.getByRole('alert').filter({ hasText: 'Host is not allowed.' })).toBeVisible()
})

test('confirm a possible new address, then reset after a confirm step', async ({ page }) => {
  const { d } = await open(page)
  await page.getByText('Source addresses').first().click()
  const alpha = page.getByRole('group', { name: 'Alpha Comics addresses' })
  const prop = alpha.getByRole('group', { name: 'Possible new address for Alpha Comics' })
  await expect(prop.getByText('go to that host')).toBeVisible()
  await prop.getByRole('button', { name: 'Confirm alpha-new.example' }).click()
  await expect(prop).toBeHidden()
  await expect(alpha.getByText('last worked')).toBeVisible()
  expect(d.calls[0]).toMatchObject({ path: '/api/source-domains/proposals/confirm', body: { source: 'alpha', host: 'alpha-new.example' } })
  await alpha.getByRole('button', { name: 'Reset Alpha Comics to defaults' }).click()
  expect(d.calls).toHaveLength(1)
  await alpha.getByRole('button', { name: 'Confirm reset to defaults' }).click()
  await expect(alpha.getByText('Default', { exact: true })).toBeVisible()
  const last = d.calls.at(-1)!
  expect(last.path).toBe('/api/source-domains/alpha/reset')
  expect(last.headers['x-baihe-local']).toBe('1')
  expect(last.headers['content-type']).toContain('application/json')
})

test('dismiss removes the proposal', async ({ page }) => {
  const { d } = await open(page)
  await page.getByText('Source addresses').first().click()
  const alpha = page.getByRole('group', { name: 'Alpha Comics addresses' })
  await alpha.getByRole('button', { name: 'Dismiss alpha-new.example' }).click()
  await expect(alpha.getByText('Possible new address')).toBeHidden()
  expect(d.calls[0].path).toBe('/api/source-domains/proposals/dismiss')
})

test('an older server without the routes hides the card', async ({ page }) => {
  await open(page, { available: false })
  await expect(page.getByText('Pacing & cache')).toBeVisible()
  await expect(page.getByText('Source addresses')).toHaveCount(0)
})
