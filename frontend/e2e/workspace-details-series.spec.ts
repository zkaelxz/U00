import { expect, test, type Page } from '@playwright/test'

// Parity P10 (the rest of Edit details), P11 ("+ New series…") and X09 (the
// series picker in the glossary box). The drama read is the real seeded
// drama 2, served as stubbed copies; the metadata save is mocked so the
// seeded library is never changed.

async function stubDrama(page: Page, patch: Record<string, unknown>) {
  const drama = await (await page.request.get('/api/library/dramas/2')).json()
  const state = { current: { ...drama, ...patch } as Record<string, unknown> }
  await page.route('**/api/library/dramas/2', (r) => r.fulfill({ json: state.current }))
  const bodies: Record<string, unknown>[] = []
  await page.route('**/api/dramas/2/metadata', (r) => {
    const body = r.request().postDataJSON() as Record<string, unknown>
    bodies.push(body)
    const next = { ...state.current, ...body }
    if ('new_series_name' in body) {
      delete next.new_series_name
      next.series_id = 41
    }
    if (body.series_id === 0) next.series_id = null
    state.current = next
    return r.fulfill({ json: next })
  })
  await page.route('**/api/library/series', (r) =>
    r.fulfill({
      json: {
        items: [
          { id: 7, name: 'Seven', character_count: 0, glossary_term_count: 0, dramas: [] },
          ...(state.current.series_id === 41
            ? [{ id: 41, name: 'Saga', character_count: 0, glossary_term_count: 0, dramas: [] }]
            : []),
        ],
      },
    }),
  )
  return bodies
}

test('Edit details shows and saves genre, status, counts, source URL and episode summary', async ({ page }) => {
  const bodies = await stubDrama(page, {
    genre: 'xianxia', publication_status: 'ongoing', chapter_count: 120, episode_number: 2,
    source_url: 'https://example.com/d/2', episode_summary: 'Before.',
  })
  await page.goto('/#/drama/2/source')
  await page.locator('.section-title', { hasText: 'Edit details' }).click()
  await expect(page.getByLabel('Genre', { exact: true })).toHaveValue('xianxia')
  await expect(page.getByLabel('Chapter count', { exact: true })).toHaveValue('120')
  await expect(page.getByLabel('Source URL', { exact: true })).toHaveValue('https://example.com/d/2')
  await page.getByLabel('Genre', { exact: true }).fill('romance')
  await page.getByLabel('Publication status', { exact: true }).selectOption('completed')
  await page.getByLabel('Chapter count', { exact: true }).fill('')
  await page.getByLabel('Episode number', { exact: true }).fill('3')
  await page.getByLabel('Running episode summary', { exact: true }).fill('After.')
  // A malformed count is caught, never read as "" (which would clear it).
  await page.getByLabel('Episode number', { exact: true }).fill('12e')
  await page.getByRole('button', { name: 'Save details' }).click()
  await expect(page.getByText('Enter a whole number, or leave it empty.')).toBeVisible()
  expect(bodies).toEqual([])
  await page.getByLabel('Episode number', { exact: true }).fill('3')
  await page.getByLabel('Source URL', { exact: true }).fill('ftp://nope')
  await page.getByRole('button', { name: 'Save details' }).click()
  await expect(page.getByText('Start the link with http:// or https://.')).toBeVisible()
  expect(bodies).toEqual([])
  await page.getByLabel('Source URL', { exact: true }).fill('https://example.org/d/2')
  await page.getByRole('button', { name: 'Save details' }).click()
  await expect.poll(() => bodies).toEqual([{
    genre: 'romance', source_url: 'https://example.org/d/2', episode_summary: 'After.',
    chapter_count: 0, episode_number: 3, publication_status: 'completed',
  }])
  await expect(page.getByRole('status').filter({ hasText: 'Details saved.' })).toBeVisible()
})

test('Edit details "+ New series…" sends the name and then shows the new series', async ({ page }) => {
  const bodies = await stubDrama(page, { series_id: null })
  await page.goto('/#/drama/2/source')
  await page.locator('.section-title', { hasText: 'Edit details' }).click()
  const series = page.getByLabel('Series', { exact: true })
  await series.selectOption('new')
  await page.getByRole('button', { name: 'Save details' }).click()
  await expect(page.getByText('Enter a name for the new series.')).toBeVisible()
  await page.getByLabel('New series name', { exact: true }).fill(' Saga ')
  await page.getByRole('button', { name: 'Save details' }).click()
  await expect.poll(() => bodies).toEqual([{ new_series_name: 'Saga' }])
  await expect(series).toHaveValue('41')
  await expect(page.getByLabel('New series name', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Save details' })).toBeDisabled()
})

test('the glossary box explains a drama without a series and creates one in one tap', async ({ page }) => {
  const bodies = await stubDrama(page, { series_id: null })
  await page.goto('/#/drama/2/translate')
  await page.locator('.section-title', { hasText: 'Glossary' }).first().click()
  const box = page.getByTestId('series-assign')
  await expect(box).toContainText("isn't in a series, so it can't hold glossary terms")
  await expect(page.getByRole('button', { name: 'Add term' })).toBeDisabled()
  await box.getByRole('button', { name: /^Create series/ }).click()
  await expect.poll(() => bodies).toEqual([{ new_series_name: "Heaven Official's Blessing" }])
  await expect(page.getByTestId('series-assign-current')).toContainText('Saga')
  await expect(page.getByRole('button', { name: 'Add term' })).toBeEnabled()
})

test('the glossary box can move the drama to another series', async ({ page }) => {
  const bodies = await stubDrama(page, { series_id: 41 })
  await page.goto('/#/drama/2/translate')
  await page.locator('.section-title', { hasText: 'Glossary' }).first().click()
  const box = page.getByTestId('series-assign')
  await expect(box.getByLabel('Series', { exact: true })).toHaveValue('41')
  await box.getByLabel('Series', { exact: true }).selectOption('7')
  await box.getByRole('button', { name: 'Move to this series' }).click()
  await expect.poll(() => bodies).toEqual([{ series_id: 7 }])
  await expect(page.getByTestId('series-assign-current')).toContainText('Seven')
})
