import { expect, test } from '@playwright/test'

// Parity X03/X04 on the Translate stage and L18/L19 in the Library. The preset
// list, apply and rename calls are mocked; the Translate config (with the
// style guidance text) comes from the real seeded API.

const PRESET = {
  id: 7, name: 'Wuxia preset', translation_engine: 'claude', engine_model: null,
  style_preset: 'subtitle', locale: 'en-GB', default_female_pronouns: 1, include_genre_notes: 0,
}

test('the style guidance follows the chosen style', async ({ page }) => {
  await page.goto('/#/drama/1/translate')
  const guidance = page.getByTestId('style-guidance')
  await page.getByText('What this style asks the translator for').click()
  const first = await guidance.innerText()
  expect(first.length).toBeGreaterThan(20)
  await page.getByLabel('Style', { exact: true }).selectOption('subtitle')
  await expect(guidance).toContainText('subtitles')
  expect(await guidance.innerText()).not.toBe(first)
})

test('applying a saved preset fills the form and starts nothing', async ({ page }) => {
  await page.route('**/api/library/presets', (r) => r.fulfill({ json: { items: [PRESET] } }))
  const bodies: unknown[] = []
  await page.route('**/api/translate-run/dramas/1/apply-preset', (r) => {
    bodies.push(r.request().postDataJSON())
    return r.fulfill({ json: {
      drama_id: 1, preset_id: 7, name: 'Wuxia preset', translation_engine: 'claude', engine_model: null,
      style_preset: 'subtitle', locale: 'en-GB', default_female_pronouns: true, include_genre_notes: false,
    } })
  })
  let runs = 0
  page.on('request', (r) => r.url().includes('/translate-run/dramas/1/run') && runs++)
  await page.goto('/#/drama/1/translate')
  const apply = page.getByRole('button', { name: 'Apply preset' })
  await expect(apply).toBeDisabled()
  await page.getByLabel('Saved preset', { exact: true }).selectOption('7')
  await apply.click()
  await expect(page.getByRole('status').filter({ hasText: 'Applied preset "Wuxia preset"' })).toBeVisible()
  expect(bodies).toEqual([{ preset_id: 7 }])
  await expect(page.getByLabel('Engine', { exact: true })).toHaveValue('claude')
  await expect(page.getByLabel('Style', { exact: true })).toHaveValue('subtitle')
  await expect(page.getByLabel('English variant', { exact: true })).toHaveValue('en-GB')
  expect(runs).toBe(0)
})

test('no saved presets, no preset picker', async ({ page }) => {
  await page.route('**/api/library/presets', (r) => r.fulfill({ json: { items: [] } }))
  await page.goto('/#/drama/1/translate')
  await expect(page.getByRole('button', { name: 'Apply tier' })).toBeVisible()
  await expect(page.getByLabel('Saved preset', { exact: true })).toHaveCount(0)
})

test('a preset and a voice can be renamed in the Library', async ({ page }) => {
  let presetName = 'Wuxia preset'
  await page.route('**/api/library/presets', (r) => r.fulfill({ json: { items: [{ ...PRESET, name: presetName }] } }))
  await page.route('**/api/library/voice-bank', (r) => r.fulfill({ json: {
    items: [{ id: 3, name: 'Narrator', language: 'en', clone_engine: null, source_drama: null, clip_available: false }],
  } }))
  const bodies: Record<string, unknown> = {}
  await page.route('**/api/library/presets/7/rename', (r) => {
    bodies.preset = r.request().postDataJSON()
    presetName = 'Palace preset'
    return r.fulfill({ json: { ...PRESET, name: presetName } })
  })
  await page.route('**/api/library/voice-bank/3/rename', (r) => {
    bodies.voice = r.request().postDataJSON()
    return r.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'That name is taken.' } } })
  })
  await page.goto('/')

  await page.locator('summary', { hasText: 'Presets' }).click()
  await page.getByRole('button', { name: 'Rename Wuxia preset' }).click()
  const input = page.getByLabel('New name for Wuxia preset')
  await page.getByRole('button', { name: 'Save name' }).click()
  await expect(page.getByRole('alert').filter({ hasText: 'already its name' })).toBeVisible()
  await input.fill('  Palace preset ')
  await page.getByRole('button', { name: 'Save name' }).click()
  await expect(page.locator('.deletable-list').getByText('Palace preset')).toBeVisible()
  expect(bodies.preset).toEqual({ name: 'Palace preset' })

  await page.locator('summary', { hasText: 'Voice bank' }).click()
  await page.getByRole('button', { name: 'Rename Narrator' }).click()
  await page.getByLabel('New name for Narrator').fill('Host')
  await page.getByRole('button', { name: 'Save name' }).click()
  await expect(page.getByText('That name is taken.')).toBeVisible()
  expect(bodies.voice).toEqual({ name: 'Host' })
  await page.getByRole('button', { name: 'Cancel' }).click()
  await expect(page.getByRole('button', { name: 'Rename Narrator' })).toBeVisible()
})
