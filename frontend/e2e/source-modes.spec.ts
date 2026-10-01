import { expect, test } from '@playwright/test'

// The seeded API (drama 1, an audio drama with no source video): where the
// lines come from is picked at the top of the Transcribe card and saved at once.
test('the transcript mode is picked in the Transcribe card and saved at once', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  const group = () => page.getByRole('radiogroup', { name: 'Where the lines come from' })
  await expect(group().getByLabel('I have a transcript')).toBeChecked()
  // Reading burned-in subtitles is offered only for a drama with a source video.
  await expect(group().getByLabel('Read burned-in subtitles')).toHaveCount(0)
  await expect(page.getByLabel('Transcript text')).toBeVisible()

  await group().getByLabel('Transcribe the audio').click()
  await expect(page.getByLabel('Transcript text')).toHaveCount(0)
  await page.reload()
  await expect(group().getByLabel('Transcribe the audio')).toBeChecked()

  // Put it back: the seeded library is shared with the other specs.
  await group().getByLabel('I have a transcript').click()
  await expect(page.getByLabel('Transcript text')).toBeVisible()
  await expect(page.getByRole('region', { name: 'Source modes' })).toHaveCount(0)
})
