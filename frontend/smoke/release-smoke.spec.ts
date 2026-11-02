import path from 'node:path'

import { expect, test } from '@playwright/test'

// Walks one title through the whole pipeline in the built app against the real API: create, attach audio, transcribe (the
// Whisper decode is faked by e2e/serve_smoke_api.py), translate (the key-free Fake engine), edit a line in Review, export SRT.
// Run: `npm run build && npm run e2e:smoke`.

test('a title goes from import to an exported subtitle file', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))

  await page.goto('/')
  await page.getByRole('button', { name: 'New title' }).click()
  await page.getByLabel('English title').fill('Smoke Title')
  await page.getByRole('button', { name: 'Create title' }).click()
  await expect(page).toHaveURL(/#\/drama\/(\d+)/)
  const base = page.url().match(/^.*#\/drama\/\d+/)![0]

  // Import
  await page.locator('input[type=file]').first().setInputFiles(path.join(process.cwd(), 'smoke', 'smoke.wav'))
  await page.getByRole('button', { name: 'Upload', exact: true }).click()
  await expect(page.getByText('Uploaded audio')).toBeVisible()

  // Transcribe
  await page.getByText('Transcribe the audio').click()
  await page.getByRole('tab', { name: 'Transcribe' }).click()
  await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
  await expect(page.getByText('3 lines', { exact: true })).toBeVisible({ timeout: 30_000 })

  // Translate
  await page.goto(`${base}/translate`)
  await page.reload()
  await page.getByLabel('AI engine', { exact: true }).selectOption({ label: 'Fake' })
  await page.getByRole('button', { name: 'Translate 3 lines' }).click()
  await expect(page.getByText('3 left')).toHaveCount(0, { timeout: 30_000 })

  // Review: the translated text is there, and an edit is saved
  const id = base.split('/').pop()
  await page.goto(`${base}/review`)
  await expect(page.getByTestId('line-en').first()).toContainText('[TEST] 你好，欢迎回来。')
  await page.getByTestId('line-en').first().click()
  await page.getByLabel('Translation').fill('Hello, welcome back.')
  await page.getByRole('heading', { level: 1 }).first().click()
  await expect.poll(async () => {
    const body = await (await page.request.get(`/api/review/dramas/${id}/lines?page=1&page_size=50&only=all`)).json()
    return (Array.isArray(body) ? body : body.lines).map((l: { en: string }) => l.en)
  }).toEqual(['Hello, welcome back.', '[TEST] 今天天气很好。', '[TEST] 我们一起去散步吧。'])

  // Export
  await page.goto(`${base}/export`)
  await page.reload()
  await expect(page.getByTestId('readiness')).toContainText('3 lines')
  const block = page.getByRole('group', { name: 'Subtitle file', exact: true })
  if ((await block.locator('details.section').first().getAttribute('open')) === null) await block.locator('summary').first().click()
  await page.getByLabel('Format', { exact: true }).selectOption('srt')
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download SRT', exact: true }).click()
  const srt = await (await download).createReadStream()
  let text = ''
  for await (const chunk of srt) text += chunk
  expect(text).toContain('Hello, welcome back.')
  expect(text).toContain('00:00:03,000 --> 00:00:05,000')
  expect(errors).toEqual([])
})
