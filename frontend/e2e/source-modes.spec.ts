import { expect, test } from '@playwright/test'

import { mediaTab } from './mediaTabs'

// The seeded API (drama 1, an audio drama with no source video): where the
// lines come from is picked on Add media and saved at once.
test('the transcript mode is picked on Add media and saved at once', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  const group = () => page.getByRole('radiogroup', { name: 'Where the lines come from' })
  // The transcript box itself is the Transcribe tab's input; it follows the mode.
  const transcriptBox = async (visible: boolean) => {
    await mediaTab(page, 'transcribe').click()
    await expect(page.getByLabel('Transcript text')).toHaveCount(visible ? 1 : 0)
    await mediaTab(page, 'add').click()
  }
  await expect(group().getByLabel('I have a transcript')).toBeChecked()
  // Reading burned-in subtitles is offered only for a drama with a source video.
  await expect(group().getByLabel('Read burned-in subtitles')).toHaveCount(0)
  await transcriptBox(true)

  await group().getByLabel('Transcribe the audio').click()
  await transcriptBox(false)
  await page.reload()
  await mediaTab(page, 'add').click()
  await expect(group().getByLabel('Transcribe the audio')).toBeChecked()

  // Put it back: the seeded library is shared with the other specs.
  await group().getByLabel('I have a transcript').click()
  await transcriptBox(true)
  await expect(page.getByRole('region', { name: 'Source modes' })).toHaveCount(0)
})

test('a double-clicked Upload sends the file once and says Uploading', async ({ page }) => {
  let uploads = 0
  await page.route('**/api/media/dramas/1/upload', async (route) => {
    uploads += 1
    await new Promise((r) => setTimeout(r, 500))
    await route.fulfill({ json: { name: 'source.mp3', size: 3, kind: 'audio' } })
  })
  await page.goto('/#/drama/1/source')
  await page.getByLabel('Audio or video file').setInputFiles({ name: 'a.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  const button = page.getByRole('button', { name: 'Upload', exact: true })
  await button.dblclick()
  await expect(page.getByRole('button', { name: 'Uploading...' })).toBeDisabled()
  await expect(page.getByText(/^Uploaded audio/)).toBeVisible()
  expect(uploads).toBe(1)
})
