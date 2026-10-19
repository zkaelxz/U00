import { expect, test, type Page } from '@playwright/test'

// The card's own fold (not the Sections inside it).
const fold = (page: Page, name: string) => page.getByRole('region', { name, exact: true }).locator(':scope > details')
const isOpen = async (page: Page, name: string) => (await fold(page, name).getAttribute('open')) !== null
const tab = (page: Page, name: string) => page.getByRole('tab', { name, exact: true })

test('six tabs, the choice survives a reload, and arrow keys move between them', async ({ page }) => {
  await page.goto('/#/settings')
  const tabs = page.getByRole('tab')
  await expect(tabs).toHaveCount(6)
  await expect(tabs).toHaveText([
    'Translation and keys', 'Costs', 'Notifications and backups', 'Account and display', 'Integrations', 'Performance and advanced',
  ])
  await tabs.first().focus()
  await page.keyboard.press('ArrowRight')
  await expect(tab(page, 'Costs')).toHaveAttribute('aria-selected', 'true')
  await expect(tab(page, 'Costs')).toBeFocused()
  await page.keyboard.press('End')
  await expect(tab(page, 'Performance and advanced')).toHaveAttribute('aria-selected', 'true')
  await page.reload()
  await expect(tab(page, 'Performance and advanced')).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('region', { name: 'Developer Mode' })).toBeVisible()
})

test('every tab starts with only its first card open', async ({ page }) => {
  await page.goto('/#/settings')
  await tab(page, 'Integrations').click()
  expect(await isOpen(page, 'Jellyfin')).toBe(true)
  for (const name of ['Web search', 'Browser extension']) expect(await isOpen(page, name)).toBe(false)

  await tab(page, 'Costs').click()
  expect(await isOpen(page, 'Spending')).toBe(true)
  for (const name of ['Spend history', 'Past costs']) expect(await isOpen(page, name)).toBe(false)

  await tab(page, 'Performance and advanced').click()
  expect(await isOpen(page, 'Jobs and GPU')).toBe(true)
  for (const name of ['Offline, paths and limits', 'Timing and splitting', 'Advanced transcription', 'Developer Mode'])
    expect(await isOpen(page, name)).toBe(false)
  // A closed card still tells the current value.
  await expect(fold(page, 'Offline, paths and limits').locator('> summary .section-summary')).toBeVisible()
})

test('a card remembers whether it is open after a reload, and shows its value while closed', async ({ page }) => {
  await page.goto('/#/settings')
  await tab(page, 'Notifications and backups').click()
  await expect(fold(page, 'Notifications')).toHaveAttribute('open', '')
  await fold(page, 'Notifications').locator('> summary').click()
  await expect(fold(page, 'Notifications').locator('> summary .section-summary')).toHaveText(/^Notify when a job finishes: (On|Off)$/)
  await fold(page, 'App updates').locator('> summary').click()
  // The fold choice is saved from the details 'toggle' event, which fires after the click returns; reloading first loses it.
  await page.waitForFunction(() => Object.keys(window.localStorage).filter((k) => k.startsWith('baihe.section.settings.card.')).length >= 2)
  await page.reload()
  await tab(page, 'Notifications and backups').click()
  expect(await isOpen(page, 'Notifications')).toBe(false)
  expect(await isOpen(page, 'App updates')).toBe(true)
})

test('Expand all and Collapse all act on the tab on screen only', async ({ page }) => {
  await page.goto('/#/settings')
  await tab(page, 'Performance and advanced').click()
  await page.getByRole('button', { name: 'Expand all' }).click()
  for (const name of ['Jobs and GPU', 'Offline, paths and limits', 'Timing and splitting', 'Advanced transcription', 'Developer Mode'])
    await expect(fold(page, name)).toHaveAttribute('open', '')
  await page.getByRole('button', { name: 'Collapse all' }).click()
  for (const name of ['Jobs and GPU', 'Offline, paths and limits', 'Timing and splitting', 'Advanced transcription', 'Developer Mode'])
    await expect(fold(page, name)).not.toHaveAttribute('open', '')
  await tab(page, 'Costs').click()
  expect(await isOpen(page, 'Spending')).toBe(true)
  await page.reload()
  await tab(page, 'Performance and advanced').click()
  expect(await isOpen(page, 'Jobs and GPU')).toBe(false)
})

test('searching "ollama" switches to the Performance and advanced tab, opens the card and its Server addresses, and Escape clears', async ({ page }) => {
  await page.goto('/#/settings')
  await tab(page, 'Translation and keys').waitFor()
  const search = page.getByRole('searchbox', { name: 'Search settings' })
  await search.fill('ollama')
  await expect(page.getByRole('tab', { name: /^Performance and advanced\s*\d/ })).toBeVisible()
  const advanced = page.getByRole('region', { name: 'Offline, paths and limits' })
  await expect(advanced).toBeVisible()
  await expect(fold(page, 'Offline, paths and limits')).toHaveAttribute('open', '')
  await expect(advanced.locator('details.section:has(> summary > .section-title:text-is("Server addresses"))')).toHaveAttribute('open', '')
  await expect(advanced.getByLabel('Ollama URL', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Developer Mode' })).toBeHidden()
  await expect(page.getByRole('status').filter({ hasText: /results?$/ })).toHaveText(/^\d+ results?$/)

  await search.press('Escape')
  await expect(search).toHaveValue('')
  await expect(page.getByRole('tab', { name: /^Performance and advanced\s*\d/ })).toHaveCount(0)
})

test('a search hit on a card the person closed opens it again, on its own tab', async ({ page }) => {
  await page.goto('/#/settings')
  await tab(page, 'Notifications and backups').click()
  await fold(page, 'Notifications').locator('> summary').click()
  expect(await isOpen(page, 'Notifications')).toBe(false)
  await tab(page, 'Translation and keys').click()
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('notify me')
  // While searching, a tab's name carries its hit count.
  await expect(page.getByRole('tab', { name: /^Notifications and backups/ })).toHaveAttribute('aria-selected', 'true')
  await expect(fold(page, 'Notifications')).toHaveAttribute('open', '')
  await expect(page.getByRole('switch', { name: /Notify when a job finishes/ })).toBeVisible()
})

test('a search with no hits says so', async ({ page }) => {
  await page.goto('/#/settings')
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('zzzz')
  await expect(page.getByText('No settings match')).toBeVisible()
})

test('deep links land on the Performance and advanced tab with the card open', async ({ page }) => {
  await page.goto('/#/settings?section=developer-mode')
  await expect(tab(page, 'Performance and advanced')).toHaveAttribute('aria-selected', 'true')
  await expect(fold(page, 'Developer Mode')).toHaveAttribute('open', '')
  await expect(page.getByRole('region', { name: 'Developer Mode' })).toBeInViewport()
  await page.goto('/#/settings?section=uploads')
  await expect(page.locator('details.section:has(> summary > .section-title:text-is("Uploads"))')).toHaveAttribute('open', '')
  await expect(fold(page, 'Offline, paths and limits')).toHaveAttribute('open', '')
})

test('Settings has no Ports card; Diagnostics owns the ports', async ({ page }) => {
  await page.goto('/#/settings')
  await tab(page, 'Performance and advanced').click()
  await page.getByRole('button', { name: 'Expand all' }).click()
  await expect(page.getByRole('region', { name: 'Ports' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Open Ports on Diagnostics' })).toHaveCount(0)
  await page.goto('/#/diagnostics?card=ports')
  await expect(page.getByRole('tab', { name: 'System' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('summary', { hasText: /^Ports/ })).toBeVisible()
})
