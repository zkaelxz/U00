import { expect, test } from './fixtures'

// The Workspace shell on a desktop: the stepper's marks and the header. The
// drama read hits the seeded API; only the progress read is mocked.

const progress = (states: Record<string, string>) => ({
  drama_id: 1, stage_index: 3, stage: 'translate', line_count: 12, untranslated_count: 2,
  flagged_count: 1, has_audio: true, has_dub_track: false, exported: false,
  stages: Object.entries(states).map(([key, state]) => ({ key, state })),
})

test('stage tabs show a tick, a dot or a ring by state, with the reason as the description', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) =>
    route.fulfill({ json: progress({ source: 'done', translate: 'current', review: 'pending', dub: 'blocked', export: 'optional' }) }))
  await page.goto('/#/drama/1/translate')
  const nav = page.getByRole('navigation', { name: 'Stages' })
  const mark = (stage: string) => nav.locator(`a[href$="/drama/1/${stage}"] .stage-mark`)
  await expect(mark('source')).toHaveText('✓')
  await expect(mark('translate')).toHaveText('●')
  await expect(mark('review')).toHaveText('○')
  await expect(mark('dub')).toHaveText('○')
  await expect(mark('export')).toHaveText('○')
  // The mark is decoration: the link's name is still its label.
  await expect(nav.getByRole('link', { name: 'Source', exact: true })).toBeVisible()
  await expect(nav.getByRole('link', { name: /^Dub/ })).toHaveAttribute('title', 'Dub: Needs lines first')
  await expect(nav.getByRole('link', { name: /^Review/ })).toHaveAttribute('title', 'Review: Not done yet · 1 flagged')
})

test('with no progress read there are no marks and no counts', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) =>
    route.fulfill({ status: 500, json: { error: { code: 'internal', message: 'boom' } } }))
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('navigation', { name: 'Stages' }).getByRole('link', { name: 'Source', exact: true })).toBeVisible()
  await expect(page.locator('nav.stage-tabs .stage-mark')).toHaveCount(0)
  await expect(page.locator('nav.stage-tabs .stage-count:not(:empty)')).toHaveCount(0)
})

test('header: Back to Library, status, media type, line count and Read are all shown', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) =>
    route.fulfill({ json: progress({ source: 'done', translate: 'current', review: 'pending', dub: 'optional', export: 'pending' }) }))
  await page.goto('/#/drama/1/translate')
  const header = page.locator('.workspace-header')
  await expect(header.getByRole('link', { name: 'Back to Library' })).toContainText('Library')
  await expect(header.locator('.ws-media')).toBeVisible()
  await expect(header.getByTestId('stage-counts')).toHaveText('12 lines')
  await expect(header.getByRole('link', { name: 'Read', exact: true })).toBeVisible()
  await header.getByRole('link', { name: 'Back to Library' }).click()
  await expect(page).toHaveURL(/#\/library$|#\/$|\/$/)
})

test('a drama the API hides from this user reads as not found and no stage loads', async ({ page }) => {
  const notFound = { error: { code: 'not_found', message: 'No drama with id 77.' } }
  await page.route('**/api/**/dramas/77**', (route) => route.fulfill({ status: 404, json: notFound }))
  await page.goto('/#/drama/77/review')
  await expect(page.getByRole('alert').first()).toContainText('could not be found')
  await expect(page.getByTestId('drama-title')).toHaveText('Drama #77')
  await expect(page.getByRole('region', { name: 'Review' })).toHaveCount(0)
})
