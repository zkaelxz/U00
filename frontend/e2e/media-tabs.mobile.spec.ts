import { expect, test } from '@playwright/test'

import { enableDeveloperMode } from './sourceHelpers'
import { expectRowInputsAligned } from './voiceDetectorLayout'
import { expectNoSidewaysScroll, gotoMedia, mediaPanel, openAllFolds } from './mediaTabs'
import { pickTab } from './tabPicker'

// Phone (390x844): the tab row, the segmented controls and every sub-tab stay inside the screen.

test('the five sub-tabs are a picker that fits the screen with every section reachable', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await expect(page.getByRole('tablist', { name: 'Media sections' })).toHaveCount(0)
  const picker = page.getByRole('button', { name: /^Media sections: / })
  await expect(picker).toBeVisible()
  expect((await picker.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await picker.click()
  const options = page.locator('.ui-tab-picker-list').getByRole('option')
  await expect(options).toHaveText(['Add media', 'Transcribe', 'Tools', 'Details', 'Credits'])
  for (const option of await options.all()) expect((await option.boundingBox())!.height).toBeGreaterThanOrEqual(44)
  await expectNoSidewaysScroll(page, 'Media tab')
})

for (const tab of ['add', 'transcribe', 'tools', 'details'] as const) {
  test(`${tab}: no sideways scroll, even with every fold open`, async ({ page }) => {
    await enableDeveloperMode(page)
    await gotoMedia(page, tab)
    await openAllFolds(mediaPanel(page, tab))
    await expectNoSidewaysScroll(page, tab)
  })
}

test('the three transcript modes are all inside the screen, including "Read burned-in subtitles"', async ({ page }) => {
  // The server offers burned-in reading only once the title has a source video.
  await page.route('**/api/source/dramas/1/config', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const resp = await route.fetch()
    const config = await resp.json()
    return route.fulfill({ response: resp, json: { ...config, transcript_mode_options: ['have_transcript', 'whisper', 'hardsub_ocr'] } })
  })
  await gotoMedia(page, 'add')
  const group = mediaPanel(page, 'add').getByRole('radiogroup', { name: 'Where the lines come from' })
  const labels = group.locator('label')
  await expect(labels).toHaveCount(3)
  const view = page.viewportSize()!
  for (let i = 0; i < 3; i++) {
    const box = (await labels.nth(i).boundingBox())!
    expect(box.x, `segment ${i} starts on screen`).toBeGreaterThanOrEqual(0)
    expect(box.x + box.width, `segment ${i} ends on screen`).toBeLessThanOrEqual(view.width)
  }
  await expect(labels.nth(2)).toContainText('Read burned-in subtitles')
  await expectNoSidewaysScroll(page, 'Add media with three modes')
})

test('the advanced options stack in one column and keep each input under its label', async ({ page }) => {
  await gotoMedia(page, 'tools')
  const advanced = mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })
  await openAllFolds(advanced)
  await expect(advanced.getByLabel('Pause that can split a long line', { exact: true })).toBeVisible()
  await expectRowInputsAligned(advanced)
  await pickTab(page, 'Transcribe', 'Media sections')
  await expectNoSidewaysScroll(page, 'Transcribe')
})
