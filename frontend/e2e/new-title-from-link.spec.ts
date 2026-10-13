import { expect, test } from '@playwright/test'

// New title with "Link (optional)": Fill from link never overwrites what was typed, only suggests the
// channel as the series, and a nameless title is created from the link. The probe and the create are
// mocked (a real read would go to the internet), so the seeded library is unchanged.

const PROBE = {
  kind: 'video', title: '配信 #12', channel: 'Some Channel', release_date: '2026-03-01', is_live: false,
  summary: '', platform: '', series_suggestion: 'Some Channel', note: '', display_url: 'https://www.youtube.com/watch',
}
const LINK = 'https://www.youtube.com/watch?v=abcdefghijk'

test('Fill from link keeps typed text and fills only the ticked empty fields', async ({ page }) => {
  const probes: { body: string; local: string | null }[] = []
  await page.route('**/api/media-link/probe', (route) => {
    probes.push({ body: route.request().postData() ?? '', local: route.request().headers()['x-baihe-local'] ?? null })
    return route.fulfill({ json: PROBE })
  })
  let created: Record<string, unknown> | null = null
  await page.route('**/api/dramas', (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    created = route.request().postDataJSON()
    return route.fulfill({ status: 201, json: { id: 99999, title_zh: 'Typed', title_from_link: null } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'New title' }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await sheet.getByLabel('Original title').fill('Typed')
  await sheet.getByRole('textbox', { name: 'Link (optional)' }).fill(LINK)
  await sheet.getByRole('button', { name: 'Fill from link' }).click()

  const said = sheet.getByRole('list', { name: 'What the link says' })
  await expect(said).toContainText('Link says: 配信 #12')
  await expect(said.getByRole('button', { name: 'Use this' })).toBeVisible()
  await expect(said.getByRole('checkbox', { name: /Stream date: 2026-03-01/ })).toBeChecked()
  expect(probes).toEqual([{ body: JSON.stringify({ url: LINK }), local: '1' }])
  // The typed title is untouched until "Use this" is pressed; the channel is only a suggestion.
  await expect(sheet.getByLabel('Original title')).toHaveValue('Typed')
  await expect(sheet.getByText('Suggested series (folder):')).toContainText('Some Channel')

  await sheet.getByRole('button', { name: 'Fill ticked fields' }).click()
  await expect(sheet.getByLabel('Stream / release date', { exact: true })).toHaveValue('2026-03-01')
  await expect(sheet.getByLabel('Original title')).toHaveValue('Typed')

  await sheet.getByRole('button', { name: 'Create title' }).click()
  await expect.poll(() => created).toMatchObject({ title_zh: 'Typed', release_date: '2026-03-01', source_url: LINK })
  expect(created).not.toHaveProperty('new_series_name')
})

test('a title with only a link is created and says when it was named after the website', async ({ page }) => {
  let created: Record<string, unknown> | null = null
  await page.route('**/api/dramas', (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    created = route.request().postDataJSON()
    return route.fulfill({ status: 201, json: { id: 99999, title_zh: 'youtube.com', title_from_link: 'placeholder' } })
  })
  await page.goto('/')
  await page.getByRole('button', { name: 'New title' }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await sheet.getByRole('textbox', { name: 'Link (optional)' }).fill(LINK)
  await sheet.getByRole('button', { name: 'Create title' }).click()
  await expect(page.getByRole('status').filter({ hasText: 'named after its website' })).toBeVisible()
  await expect(page.getByRole('link', { name: 'Open the title' })).toHaveAttribute('href', /#\/drama\/99999/)
  expect(created).toEqual({ source_language: expect.any(String), media_type: expect.any(String), source_url: LINK })
})
