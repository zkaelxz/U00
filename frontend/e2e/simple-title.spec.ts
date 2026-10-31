import { expect, test } from './fixtures'

// Audio and video titles have a Simple view (#/drama/1, no stage) beside today's workspace; the choice is
// remembered in the browser. Title 1 is the seeded audio drama, 3 the seeded video, 2 the novel.

const progress = (id: number, lines: number, untranslated: number) => ({
  drama_id: id, stage_index: 1, stage: 'translate', line_count: lines, untranslated_count: untranslated,
  flagged_count: 0, has_audio: false, has_dub_track: false, exported: false, stages: [],
})

test('Simple shows the five actions and the episode, and Full brings the stage tabs back', async ({ page }) => {
  await page.route('**/api/workflow/dramas/1/progress', (route) => route.fulfill({ json: progress(1, 10, 10) }))
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
  await page.getByRole('radio', { name: 'Simple' }).check({ force: true })
  await expect(page).toHaveURL(/#\/drama\/1$/)
  await expect(page.getByRole('navigation', { name: 'Stages' })).toHaveCount(0)
  await expect(page.getByRole('link', { name: 'Transcribe and translate' })).toHaveAttribute('href', '#/drama/1/translate')
  await expect(page.getByRole('link', { name: 'Add media' })).toHaveAttribute('href', '#/drama/1/source?tab=add')
  await expect(page.getByRole('link', { name: 'Glossary' })).toHaveAttribute('href', '#/drama/1/translate?tab=glossary')
  await expect(page.getByRole('link', { name: 'Review' })).toHaveAttribute('href', '#/drama/1/review')
  await expect(page.getByRole('link', { name: 'Export' })).toHaveAttribute('href', '#/drama/1/export')
  const episodes = page.getByRole('list', { name: 'Episodes' })
  await expect(episodes.getByRole('listitem')).toHaveCount(1)
  await expect(episodes).toContainText('Transcribed · Not translated')
  await page.getByRole('radio', { name: 'Full' }).check({ force: true })
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
})

test('the choice is remembered, and a link that names a stage still opens that stage', async ({ page }) => {
  await page.route('**/api/workflow/dramas/3/progress', (route) => route.fulfill({ json: progress(3, 0, 0) }))
  await page.goto('/#/drama/3')
  await page.getByRole('radio', { name: 'Simple' }).check({ force: true })
  await expect(page.getByRole('link', { name: 'Transcribe and translate' })).toHaveAttribute('href', '#/drama/3/source?tab=transcribe')
  await expect(page.getByRole('list', { name: 'Episodes' })).toContainText('Not transcribed')
  await page.reload()
  await expect(page.getByRole('link', { name: 'Transcribe and translate' })).toBeVisible()
  await page.goto('/#/drama/3/review')
  await expect(page.getByRole('navigation', { name: 'Stages' })).toBeVisible()
})

test('a novel has no Simple / Full switch', async ({ page }) => {
  await page.goto('/#/drama/2')
  await expect(page.getByTestId('drama-title')).toBeVisible()
  await expect(page.getByRole('radio', { name: 'Simple' })).toHaveCount(0)
})
