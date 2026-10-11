import { expect, test } from '@playwright/test'

import { DRAMA_ID, mockMakeSubtitles } from './makeSubtitlesMocks'

// File to English SRT from the Library. Every API call is mocked; nothing is
// created, transcribed or translated for real.

const AUDIO = { name: 'My Show.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') }
const card = (page: import('@playwright/test').Page) => page.getByRole('region', { name: 'Make subtitles' })

test('four clicks: choose a file and language, make subtitles, download', async ({ page }) => {
  const m = await mockMakeSubtitles(page)
  await page.goto('/')
  const c = card(page)
  const primary = c.getByRole('button', { name: 'Make subtitles' })
  await expect(primary).toBeDisabled()
  await expect(c.getByText('Still needed: an audio or video file.')).toBeVisible()
  expect(m.created).toHaveLength(0)

  await c.getByLabel('Audio or video file').setInputFiles(AUDIO)
  await c.getByLabel('Source language').selectOption('ja')
  await expect(primary).toBeEnabled()
  expect(m.created, 'nothing is called before the primary').toHaveLength(0)
  await primary.click()

  const download = page.waitForEvent('download')
  await c.getByRole('button', { name: 'Download SRT' }).click()
  const d = await download
  expect(d.suggestedFilename()).toBe('My Show.srt')
  expect(m.created).toEqual([{ source_language: 'ja', media_type: 'audio_drama', title_en: 'My Show' }])
  expect(m.uploads).toHaveLength(1)
  expect(m.translateRuns).toHaveLength(1)
  expect(m.translateRuns[0]).toMatchObject({ engine: 'deepseek' })
  await expect(c.getByRole('link', { name: 'Review lines' })).toHaveAttribute('href', `#/drama/${DRAMA_ID}/review`)
  await expect(c.getByRole('link', { name: 'Open title' })).toHaveAttribute('href', `#/drama/${DRAMA_ID}`)
})

test('shows the steps while it runs and a reload reattaches to the job', async ({ page }) => {
  const m = await mockMakeSubtitles(page, { holdFirst: true })
  await page.goto('/')
  const c = card(page)
  await c.getByLabel('Audio or video file').setInputFiles(AUDIO)
  await c.getByRole('button', { name: 'Make subtitles' }).click()
  const status = c.getByRole('status')
  await expect(status.getByRole('listitem')).toHaveText([/Upload/, /Transcribe 40%/, /Translate/, /Export/])
  await expect(status.locator('[aria-current="step"]')).toHaveText(/Transcribe/)
  await expect(status).toBeFocused()

  await page.reload()
  await expect(card(page).getByRole('status').locator('[aria-current="step"]')).toHaveText(/Transcribe/)
  expect(m.created, 'the reload does not start another run').toHaveLength(1)

  m.release()
  await expect(card(page).getByRole('button', { name: 'Download SRT' })).toBeVisible()
})

test('a failure names its step and links to that stage', async ({ page }) => {
  await mockMakeSubtitles(page, { failJob: 'translate' })
  await page.goto('/')
  const c = card(page)
  await c.getByLabel('Audio or video file').setInputFiles(AUDIO)
  await c.getByRole('button', { name: 'Make subtitles' }).click()
  await expect(c.getByRole('alert')).toBeVisible()
  await expect(c.getByText('Failed at Translate.')).toBeVisible()
  await expect(c.getByRole('link', { name: 'Fix in Translate' })).toHaveAttribute('href', `#/drama/${DRAMA_ID}/translate`)
  // The failure is the first thing in the card, above the form it asks the viewer to fix.
  const failedY = (await c.getByText('Failed at Translate.').boundingBox())!.y
  expect(failedY).toBeLessThan((await c.getByLabel('Audio or video file').boundingBox())!.y)
  expect(failedY).toBeLessThan((await c.getByRole('button', { name: 'Make subtitles' }).boundingBox())!.y)
})

test('a video file creates a video drama and the title can be changed', async ({ page }) => {
  const m = await mockMakeSubtitles(page)
  await page.goto('/')
  const c = card(page)
  await c.getByLabel('Audio or video file').setInputFiles({ name: 'ep1.mp4', mimeType: 'video/mp4', buffer: Buffer.from('abc') })
  await c.getByText('Options').click()
  await c.getByLabel('Title').fill('Episode One')
  await c.getByLabel('English variant').selectOption('en-GB')
  await c.getByRole('button', { name: 'Make subtitles' }).click()
  await expect(c.getByRole('button', { name: 'Download SRT' })).toBeVisible()
  expect(m.created).toEqual([{ source_language: 'zh', media_type: 'video_drama', title_en: 'Episode One' }])
  expect(m.translateRuns[0]).toMatchObject({ locale: 'en-GB' })
})

test('Make another clears the typed title but keeps the file-independent choices', async ({ page }) => {
  const m = await mockMakeSubtitles(page)
  await page.goto('/')
  const c = card(page)
  await c.getByLabel('Audio or video file').setInputFiles(AUDIO)
  await c.getByText('Options').click()
  await c.getByLabel('Title').fill('Typed Title')
  await c.getByLabel('English variant').selectOption('en-GB')
  await c.getByRole('button', { name: 'Make subtitles' }).click()
  await c.getByRole('button', { name: 'Make another' }).click()

  await expect(c.getByLabel('Title')).toHaveValue('')
  await expect(c.getByLabel('English variant')).toHaveValue('en-GB')
  await c.getByLabel('Audio or video file').setInputFiles({ name: 'Second.mp3', mimeType: 'audio/mpeg', buffer: Buffer.from('abc') })
  await c.getByRole('button', { name: 'Make subtitles' }).click()
  await expect(c.getByRole('button', { name: 'Download SRT' })).toBeVisible()
  expect(m.created.map((x) => (x as { title_en: string }).title_en)).toEqual(['Typed Title', 'Second'])
})

test('Cancel during the upload aborts it and says the title was kept', async ({ page }) => {
  const m = await mockMakeSubtitles(page, { holdUpload: true })
  await page.goto('/')
  const c = card(page)
  await c.getByLabel('Audio or video file').setInputFiles(AUDIO)
  await c.getByRole('button', { name: 'Make subtitles' }).click()
  await expect(c.getByRole('status').locator('[aria-current="step"]')).toHaveText(/Upload/)
  await c.getByRole('button', { name: 'Cancel' }).click()

  await expect(c.getByText('Cancelled. The title was kept.')).toBeVisible()
  await expect(c.getByRole('link', { name: 'Open title' })).toHaveAttribute('href', `#/drama/${DRAMA_ID}`)
  await expect(c.getByRole('alert')).toHaveCount(0)
  expect(m.translateRuns).toHaveLength(0)
})

test('a job cancelled elsewhere shows the same note instead of resetting silently', async ({ page }) => {
  await mockMakeSubtitles(page, { cancelledJob: true })
  await page.goto('/')
  const c = card(page)
  await c.getByLabel('Audio or video file').setInputFiles(AUDIO)
  await c.getByRole('button', { name: 'Make subtitles' }).click()
  await expect(c.getByText('Cancelled. The title was kept.')).toBeVisible()
  await expect(c.getByRole('link', { name: 'Open title' })).toHaveAttribute('href', `#/drama/${DRAMA_ID}`)
})

test('a run saved at the upload step shows a note with a link to the title on reload', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.addInitScript((id) => {
    if (!localStorage.getItem('baihe.pref.makeSubtitles.run')) localStorage.setItem('baihe.pref.makeSubtitles.run', JSON.stringify({ dramaId: id, step: 'upload' }))
  }, DRAMA_ID)
  await page.goto('/')
  const c = card(page)
  await expect(c.getByText('The last run stopped before the upload finished.')).toBeVisible()
  await expect(c.getByRole('link', { name: 'Open title' })).toHaveAttribute('href', `#/drama/${DRAMA_ID}`)
})
