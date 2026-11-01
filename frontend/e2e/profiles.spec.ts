import { expect, test } from '@playwright/test'
import { openFold } from './foldHelpers'

// Translation setting profiles: pick one on New title (the default is preselected), and
// edit, make default or create one in Library tools. The profile list and the writes
// are mocked; the engine and field lists come from the real seeded API.

const profile = (o: Record<string, unknown> = {}) => ({
  id: 7, name: 'Wuxia', translation_engine: 'claude', engine_model: null, style_preset: 'subtitle',
  locale: null, default_female_pronouns: 0, include_genre_notes: 1, is_default: false,
  options: { batch_size: 12, reflect: true, beam_size: 7 }, ...o,
})

test('New title preselects the default profile and sends the chosen one', async ({ page }) => {
  await page.route('**/api/library/presets', (r) => r.fulfill({ json: { items: [
    profile({ id: 7, name: 'Wuxia', is_default: true }), profile({ id: 8, name: 'Quick draft', is_default: false }),
  ] } }))
  await page.route('**/api/dramas', (r) => r.request().method() === 'POST'
    ? r.fulfill({ status: 409, json: { error: { code: 'conflict', message: 'Stopped by the test.' } } })
    : r.fallback())
  await page.goto('/')
  await page.getByRole('button', { name: 'New title' }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  const select = sheet.getByLabel('Profile', { exact: true })
  await expect(select).toHaveValue('7')
  await expect(select.locator('option:checked')).toHaveText('Wuxia (default)')
  await sheet.getByLabel('English title').fill('Profile title')
  let req = page.waitForRequest((r) => r.url().endsWith('/api/dramas') && r.method() === 'POST')
  await sheet.getByRole('button', { name: 'Create title', exact: true }).click()
  expect((await req).postDataJSON()).toMatchObject({ preset_id: 7, source_language: 'zh' })

  await select.selectOption('8')
  req = page.waitForRequest((r) => r.url().endsWith('/api/dramas') && r.method() === 'POST')
  await sheet.getByRole('button', { name: 'Create title', exact: true }).click()
  expect((await req).postDataJSON()).toMatchObject({ preset_id: 8 })

  await select.selectOption('')
  req = page.waitForRequest((r) => r.url().endsWith('/api/dramas') && r.method() === 'POST')
  await sheet.getByRole('button', { name: 'Create title', exact: true }).click()
  expect((await req).postDataJSON().preset_id).toBeUndefined()
})

test('a profile can be edited, made the default and created in Library tools', async ({ page }) => {
  await page.route('**/api/library/presets', (r) => r.fulfill({ json: { items: [profile()] } }))
  const sent: Record<string, unknown> = {}
  await page.route('**/api/translate-run/presets/7/update', (r) => {
    sent.update = r.request().postDataJSON()
    return r.fulfill({ json: { preset: profile({ name: 'Wuxia v2' }) } })
  })
  await page.route('**/api/translate-run/presets/7/default', (r) => {
    sent.default = r.request().postDataJSON()
    return r.fulfill({ json: profile({ is_default: true }) })
  })
  await page.route('**/api/translate-run/presets', (r) => {
    sent.create = r.request().postDataJSON()
    return r.fulfill({ json: { preset: profile({ id: 9, name: 'Fresh' }), replaced: false } })
  })
  await page.goto('/#/library-tools')
  await openFold(page, 'Profiles')

  await page.getByRole('button', { name: 'Make Wuxia the default' }).click()
  expect(sent.default).toEqual({ is_default: true })

  await page.getByRole('button', { name: 'Edit Wuxia' }).click()
  const editor = page.getByRole('group', { name: 'Edit profile Wuxia' })
  await expect(editor.getByLabel('Batch size', { exact: true })).toHaveValue('12')
  await expect(editor.getByLabel('Profile name', { exact: true })).toHaveValue('Wuxia')
  await expect(editor.getByLabel(/English variant|Glossary/)).toHaveCount(0)
  // The source language is only for Quick video translate, in its own section.
  await expect(editor.getByLabel('Source language', { exact: true })).toHaveCount(1)
  await editor.getByLabel('Profile name', { exact: true }).fill('Wuxia v2')
  await editor.getByLabel('Batch size', { exact: true }).fill('15')
  await editor.getByRole('button', { name: 'Save changes' }).click()
  await expect(editor).toHaveCount(0)
  expect(sent.update).toMatchObject({
    name: 'Wuxia v2', translation_engine: 'claude', locale: null, style_preset: 'subtitle',
    options: { batch_size: 15, reflect: true, beam_size: 7 },
  })

  await page.getByRole('button', { name: 'New profile' }).click()
  const fresh = page.getByRole('group', { name: 'New profile' })
  await fresh.getByRole('button', { name: 'Save profile' }).click()
  await expect(fresh.getByRole('alert')).toContainText('name')
  await fresh.getByLabel('Profile name', { exact: true }).fill('Fresh')
  await fresh.getByRole('button', { name: 'Save profile' }).click()
  expect(sent.create).toMatchObject({ name: 'Fresh', locale: null })
})
