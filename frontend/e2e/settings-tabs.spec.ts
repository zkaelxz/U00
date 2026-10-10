import { expect, test } from '@playwright/test'

test('three tabs, the choice survives a reload, and arrow keys move between them', async ({ page }) => {
  await page.goto('/#/settings')
  const tabs = page.getByRole('tab')
  await expect(tabs).toHaveCount(3)
  await tabs.first().focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Preferences' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('tab', { name: 'Preferences' })).toBeFocused()
  await page.keyboard.press('End')
  await expect(page.getByRole('tab', { name: 'System' })).toHaveAttribute('aria-selected', 'true')
  await page.reload()
  await expect(page.getByRole('tab', { name: 'System' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('region', { name: 'Developer Mode' })).toBeVisible()
})

test('searching "ollama" shows the Advanced card with Server addresses open, counts the tab, and Escape clears', async ({ page }) => {
  await page.goto('/#/settings')
  await page.getByRole('tab', { name: 'Translation and keys' }).waitFor()
  const search = page.getByRole('searchbox', { name: 'Search settings' })
  await search.fill('ollama')
  await expect(page.getByRole('tab', { name: /^System\s*\d/ })).toBeVisible()
  const advanced = page.getByRole('region', { name: 'Advanced' })
  await expect(advanced).toBeVisible()
  await expect(advanced.locator('details.section:has(> summary > .section-title:text-is("Server addresses"))')).toHaveAttribute('open', '')
  await expect(advanced.getByLabel('Ollama URL', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Developer Mode' })).toBeHidden()
  await expect(page.getByRole('status').filter({ hasText: /results?$/ })).toHaveText(/^\d+ results?$/)

  await search.press('Escape')
  await expect(search).toHaveValue('')
  await expect(page.getByRole('tab', { name: /^System\s*\d/ })).toHaveCount(0)
})

test('a search with no hits says so', async ({ page }) => {
  await page.goto('/#/settings')
  await page.getByRole('searchbox', { name: 'Search settings' }).fill('zzzz')
  await expect(page.getByText('No settings match')).toBeVisible()
})

test('deep links land on the System tab', async ({ page }) => {
  await page.goto('/#/settings?section=developer-mode')
  await expect(page.getByRole('tab', { name: 'System' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('region', { name: 'Developer Mode' })).toBeInViewport()
  await page.goto('/#/settings?section=uploads')
  await expect(page.locator('details.section:has(> summary > .section-title:text-is("Uploads"))')).toHaveAttribute('open', '')
})

test('the Ports link goes to Diagnostics', async ({ page }) => {
  await page.goto('/#/settings')
  await page.getByRole('tab', { name: 'System' }).click()
  await page.getByRole('link', { name: 'Open Ports on Diagnostics' }).click()
  await expect(page).toHaveURL(/#\/diagnostics$/)
})
