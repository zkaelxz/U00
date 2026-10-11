import { expect, test } from './fixtures'
import { openSettingsGroups } from './settingsNav'

test('settings toggles round-trip and keys are yes/no only', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Preferences')
  const box = page.getByRole('switch', { name: /Notify when a job finishes/ })
  await expect(box).toBeVisible()
  const before = await box.isChecked()

  // The toggle updates optimistically; wait for the save to finish before reloading,
  // or the reload can read the old server value.
  const saved = () => page.waitForResponse((r) => r.url().endsWith('/api/settings') && r.request().method() === 'POST')
  await Promise.all([saved(), box.click()])
  await expect(box).toBeChecked({ checked: !before })
  await page.reload()
  await openSettingsGroups(page, 'Preferences')
  await expect(box).toBeChecked({ checked: !before })

  await Promise.all([saved(), box.click()]) // restore
  await expect(box).toBeChecked({ checked: before })
  for (const dd of await page.locator('[data-testid^="key-"]').all())
    await expect(dd).toHaveText(/^(Set|Missing|Not needed)$/) // set/missing only, never a key
})

test('a failed update rolls the toggle back and shows an error', async ({ page }) => {
  await page.route('**/api/settings', (route) =>
    route.request().method() === 'POST'
      ? route.fulfill({
          status: 422,
          contentType: 'application/json',
          body: JSON.stringify({ error: { code: 'invalid_input', message: 'Rejected.' } }),
        })
      : route.continue(),
  )
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'System')
  const box = page.getByRole('switch', { name: /Use the GPU/ })
  const before = await box.isChecked()
  await box.click()
  await expect(page.getByRole('alert')).toBeVisible()
  await expect(box).toBeChecked({ checked: before })
})

test('Engines and keys: one list with key status on every row, Set key opens that form in place, and the page fits a phone', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  const card = page.getByRole('region', { name: 'Which engine does what' })
  await expect(card.locator('.card-meta')).toHaveText(/^\d+ of \d+ keys set/)
  const list = card.getByRole('list', { name: 'Engines' })
  await expect(list.getByRole('listitem').first()).toBeVisible()
  // One row per engine: the keyed engines, the key-less local ones and the key-only rows.
  for (const name of ['Claude', 'Ollama', 'Groq', 'Hugging Face']) await expect(list.getByText(name, { exact: true })).toBeVisible()
  // Server addresses have their own block; no raw ids like hf_token here.
  await expect(list).not.toContainText('_')
  await expect(card.getByRole('textbox')).toHaveCount(0)

  const open = card.getByRole('button', { name: /^(Set key for Claude|Replace Claude key)$/ })
  await expect(open).toHaveAttribute('aria-expanded', 'false')
  await open.click()
  const input = card.getByLabel('Claude key', { exact: true })
  await expect(input).toHaveAttribute('type', 'password')
  await expect(input).toHaveValue('')
  await expect(input).toBeFocused()
  await expect(card.getByRole('button', { name: 'Save key' })).toBeDisabled()
  await card.getByRole('button', { name: 'Close Claude key' }).click()
  await expect(card.getByRole('textbox')).toHaveCount(0)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('away from the PC the engine rows show key status only, with no Set buttons and a disabled Test', async ({ page }) => {
  await page.route('**/api/meta', async (route) => {
    const resp = await route.fetch()
    return route.fulfill({ response: resp, json: { ...(await resp.json()), local: false } })
  })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'Translation and keys')
  const card = page.getByRole('region', { name: 'Which engine does what' })
  await expect(card.getByText('Choosing engines, testing and setting keys is PC only.')).toBeVisible()
  await expect(card.locator('[data-testid^="key-"]').first()).toHaveText(/^(Set|Missing)$/)
  await expect(card.getByRole('button', { name: /^(Set key for|Replace|Close)/ })).toHaveCount(0)
  for (const b of await card.getByRole('button', { name: /^Test / }).all()) await expect(b).toBeDisabled()
})

test('settings booleans are keyboard-operable switches', async ({ page }) => {
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'System')
  const system = page.locator('#settings-panel-system')
  for (const name of [/Limit GPU jobs/, /Use the GPU/, /Free Ollama/, /Resume batches on start/])
    await expect(system.getByRole('switch', { name })).toBeVisible()
  await expect(page.getByRole('checkbox')).toHaveCount(0)
  await openSettingsGroups(page, 'Preferences')
  await expect(page.getByRole('switch', { name: 'Extension bridge' })).toBeVisible()
  await openSettingsGroups(page, 'Translation and keys')
  // Keys live in the one engine list, with the .env explanation.
  await expect(page.getByRole('region', { name: 'Which engine does what' }).getByText('Saved on the Baihe PC and never shown again.')).toBeVisible()
  const sw = page.getByRole('switch', { name: /Gemini free tier/ })
  const before = (await sw.getAttribute('aria-checked')) === 'true'
  const saved = () => page.waitForResponse((r) => r.url().endsWith('/api/settings') && r.request().method() === 'POST')

  await sw.focus()
  await expect(sw).toBeFocused()
  await Promise.all([saved(), page.keyboard.press('Space')])
  await expect(sw).toHaveAttribute('aria-checked', String(!before))
  await Promise.all([saved(), page.keyboard.press('Enter')]) // restore
  await expect(sw).toHaveAttribute('aria-checked', String(before))

  // Clicking the visible label flips the switch too (label htmlFor -> button).
  await Promise.all([saved(), page.locator('label', { hasText: 'Gemini free tier' }).click()])
  await expect(sw).toHaveAttribute('aria-checked', String(!before))
  await Promise.all([saved(), sw.click()]) // restore
  await expect(sw).toHaveAttribute('aria-checked', String(before))
})

test('a collapsible section shows a summary and starts closed on every visit', async ({ page }) => {
  await page.setViewportSize({ width: 400, height: 800 })
  await page.goto('/#/settings')
  await openSettingsGroups(page, 'System')
  const details = () => page.locator('details.section:has(> summary > .section-title:text-is("Server addresses"))')
  await expect(details().locator('.section-summary')).toHaveText(/\d+ of \d+ set/)
  await expect(details()).not.toHaveAttribute('open', '')

  await details().locator('summary').click()
  await expect(details()).toHaveAttribute('open', '')
  await expect(details().locator('.section-summary')).toHaveCount(0)
  expect(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('baihe.section.settings.')))).toEqual([])

  await page.reload()
  await expect(page.locator('details.section[open]')).toHaveCount(0)
  await openSettingsGroups(page, 'System')
  await expect(details()).not.toHaveAttribute('open', '')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('no two cards across the settings tabs share a title', async ({ page }) => {
  await page.goto('/#/settings')
  const titles: string[] = []
  for (const tab of ['Translation and keys', 'Preferences', 'System'] as const) {
    await openSettingsGroups(page, tab)
    titles.push(...(await page.locator('.settings-panel:not([hidden]) .card-title').allTextContents()))
  }
  expect(titles).toContain('Notify me')
  expect(titles).toContain('Batch resume')
  const dupes = titles.filter((t, i) => titles.indexOf(t) !== i)
  expect(dupes).toEqual([])
})
