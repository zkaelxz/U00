import { expect, test } from '@playwright/test'

import { VIDEO_LINK, mockAddLink, probeOf } from './addBoxMocks'
import { DRAMA_ID, mockMakeSubtitles } from './makeSubtitlesMocks'

// The Add box on the Library: a link or a file, what it found, what the main button does, and the
// two ways into New title. The run itself shows on the Add page. Every write is mocked.

const box = (page: import('@playwright/test').Page) => page.getByRole('region', { name: 'Add' })
const VIDEO = { name: 'ep1.mp4', mimeType: 'video/mp4', buffer: Buffer.from('abc') }

test('a video link says what the probe found and starts One-Click Translate, whose run shows on the Add page', async ({ page }) => {
  const m = await mockAddLink(page, probeOf({ title: '第一回', platform: 'YouTube', channel: 'Studio', release_date: '2026-10-02' }))
  await page.goto('/')
  const b = box(page)
  await b.getByRole('textbox', { name: 'Paste a link' }).fill(VIDEO_LINK)
  await expect(b.getByTestId('add-found')).toHaveText('Found: a video on YouTube: “第一回”, from Studio, 2026-10-02.')
  await expect(b.getByTestId('add-plan')).toHaveText('Download it on this PC, transcribe it, then translate it with DeepSeek (profile “Fast”), through the Queue.')
  const make = b.getByRole('button', { name: 'Make English subtitles' })
  // The settings stay on the button's tooltip, and under Change options for touch and keyboard.
  await expect(make).toHaveAttribute('title', /Batch size: 30/)
  await b.getByText('Change options', { exact: true }).click()
  await expect(b.getByText('Profile: Fast')).toBeVisible()
  await expect(b.getByRole('button', { name: 'Help: One-Click Translate settings' })).toBeVisible()
  await b.getByRole('checkbox', { name: 'Watch once' }).check()
  await make.click()
  await expect(page).toHaveURL(/#\/make-subtitles$/)
  expect(m.started).toEqual([{ url: VIDEO_LINK, watch_once: true }])
  await expect(page.getByRole('region', { name: 'From a link' })).toContainText('Episode 1 · Downloading')
})

test('a typed address is read once, after the typing stops; Clear empties the box', async ({ page }) => {
  const m = await mockAddLink(page, probeOf({ title: 'Ep', platform: 'YouTube' }))
  await page.goto('/')
  const b = box(page)
  const field = b.getByRole('textbox', { name: 'Paste a link' })
  await field.pressSequentially(VIDEO_LINK, { delay: 30 })
  await expect(b.getByTestId('add-found')).toHaveText('Found: a video on YouTube: “Ep”.')
  // Half-typed addresses ("https://w", "https://ww", ...) are never sent to the PC to fetch.
  expect(m.probed).toEqual([VIDEO_LINK])
  expect(m.settingsReads).toBe(1)
  await b.getByRole('button', { name: 'Clear', exact: true }).click()
  await expect(field).toHaveValue('')
  await expect(b.getByTestId('add-found')).toHaveCount(0)
  await expect(b.getByRole('button', { name: 'Add a title by hand' })).toBeVisible()
  // Enter takes the address at once.
  await field.pressSequentially('https://www.youtube.com/watch?v=xyz', { delay: 30 })
  await field.press('Enter')
  await expect(b.getByTestId('add-found')).toBeVisible()
  expect(m.probed).toEqual([VIDEO_LINK, 'https://www.youtube.com/watch?v=xyz'])
})

test('a Sources page link only adds a title', async ({ page }) => {
  await mockAddLink(page, probeOf({ kind: 'source', title: 'Book' }))
  await page.goto('/')
  const b = box(page)
  await b.getByRole('textbox', { name: 'Paste a link' }).fill('https://novels.example/book/5')
  await expect(b.getByTestId('add-found')).toHaveText('Found: a Sources page on novels.example: “Book”.')
  await expect(b.getByRole('button', { name: 'Make English subtitles' })).toHaveCount(0)
  await b.getByRole('button', { name: "Only add it, don't start" }).click()
  await expect(page.getByRole('dialog', { name: 'New title' }).getByLabel(/^Link/)).toHaveValue('https://novels.example/book/5')
})

test('a file hands its choices to the Add page, which runs it there', async ({ page }) => {
  const m = await mockMakeSubtitles(page)
  await page.goto('/')
  const b = box(page)
  await b.getByLabel('Audio or video file').setInputFiles(VIDEO)
  await expect(b.getByTestId('add-found')).toHaveText('Found: a video file, “ep1.mp4”, 3 B.')
  await b.getByText('Change options', { exact: true }).click()
  await b.getByLabel('Title').fill('Episode One')
  await b.getByLabel('English variant').selectOption('en-GB')
  expect(m.created, 'nothing runs on the Library').toHaveLength(0)
  await b.getByRole('button', { name: 'Make English subtitles' }).click()
  await expect(page).toHaveURL(/#\/make-subtitles$/)
  await expect(box(page).getByRole('button', { name: 'Download SRT' })).toBeVisible()
  expect(m.created).toEqual([{ source_language: 'zh', media_type: 'video_drama', title_en: 'Episode One' }])
  expect(m.translateRuns[0]).toMatchObject({ locale: 'en-GB' })
  await expect(box(page).getByRole('link', { name: 'Review lines' })).toHaveAttribute('href', `#/drama/${DRAMA_ID}/review`)
})

test('Clear puts a chosen file back, keeps the chosen language, and the same file can be chosen again', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.goto('/')
  const b = box(page)
  await b.getByLabel('Audio or video file').setInputFiles(VIDEO)
  await b.getByText('Change options', { exact: true }).click()
  await b.getByLabel('Source language').selectOption('ja')
  await b.getByRole('button', { name: 'Clear', exact: true }).click()
  await expect(b.getByTestId('add-found')).toHaveCount(0)
  await expect(b.getByRole('button', { name: 'Add a title by hand' })).toBeVisible()
  await expect(b.getByLabel('Audio or video file')).toHaveValue('')
  await b.getByLabel('Audio or video file').setInputFiles(VIDEO)
  await expect(b.getByTestId('add-found')).toHaveText('Found: a video file, “ep1.mp4”, 3 B.')
  await expect(b.getByTestId('add-plan')).toHaveText(/^Transcribe it as Japanese speech/)
})

test('a wrong file type is refused on the Library before anything is made or handed on', async ({ page }) => {
  const m = await mockMakeSubtitles(page)
  await page.goto('/')
  const b = box(page)
  await b.getByLabel('Audio or video file').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('abc') })
  await b.getByRole('button', { name: 'Make English subtitles' }).click()
  await expect(b.getByRole('alert')).toHaveText(/^That file type is not supported\./)
  await expect(page).not.toHaveURL(/make-subtitles/)
  expect(m.created).toHaveLength(0)
})

test('Only add it with a file opens New title with its name and type, and says the file is not attached', async ({ page }) => {
  await mockMakeSubtitles(page)
  await page.goto('/')
  await box(page).getByLabel('Audio or video file').setInputFiles(VIDEO)
  await box(page).getByRole('button', { name: "Only add it, don't start" }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await expect(sheet.getByLabel('English title')).toHaveValue('ep1')
  await expect(sheet.getByLabel('Media type')).toHaveValue('video_drama')
  await expect(sheet.getByTestId('new-title-file-note')).toHaveText('“ep1.mp4” is not attached. Create the title, then choose the file again in its Transcribe step.')
})

test('Add a title by hand opens the plain New title form', async ({ page }) => {
  await page.goto('/')
  await box(page).getByRole('button', { name: 'Add a title by hand' }).click()
  const sheet = page.getByRole('dialog', { name: 'New title' })
  await expect(sheet.getByLabel('English title')).toHaveValue('')
  await expect(sheet.getByLabel(/^Link/)).toHaveValue('')
  await expect(sheet.getByTestId('new-title-file-note')).toHaveCount(0)
})

test('Only add it on the Add page opens the Library New title with the link', async ({ page }) => {
  await mockAddLink(page, probeOf({ title: 'Ep' }))
  await page.goto('/#/make-subtitles')
  await box(page).getByRole('textbox', { name: 'Paste a link' }).fill(VIDEO_LINK)
  await box(page).getByRole('button', { name: "Only add it, don't start" }).click()
  await expect(page.getByRole('heading', { name: 'Library', level: 1 })).toBeVisible()
  await expect(page.getByRole('dialog', { name: 'New title' }).getByLabel(/^Link/)).toHaveValue(VIDEO_LINK)
})
