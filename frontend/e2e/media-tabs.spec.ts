import { expect, test } from '@playwright/test'

import { enableDeveloperMode, openAdvanced } from './sourceHelpers'
import { expectRowInputsAligned } from './voiceDetectorLayout'
import { expectNoSidewaysScroll, gotoMedia, mediaPanel, mediaTab, openAllFolds, selectMediaTab } from './mediaTabs'

// The Media stage is one screen per sub-tab. Seeded titles: 1 audio drama, 2 novel, 3 video drama.

const tabNames = (page: import('@playwright/test').Page) =>
  page.getByRole('tablist', { name: 'Media sections' }).getByRole('tab').allTextContents()

test('an audio or video title has Add media, Transcribe and Details', async ({ page }) => {
  for (const id of [1, 3]) {
    await page.goto(`/#/drama/${id}/source`)
    await expect(page.getByRole('tablist', { name: 'Media sections' })).toBeVisible()
    expect(await tabNames(page)).toEqual(['Add media', 'Transcribe', 'Details'])
  }
})

test('a novel title hides Transcribe', async ({ page }) => {
  await page.goto('/#/drama/2/source')
  await expect(page.getByRole('tablist', { name: 'Media sections' })).toBeVisible()
  expect(await tabNames(page)).toEqual(['Add media', 'Details'])
  await expect(page.getByRole('region', { name: 'Advanced options' })).toHaveCount(0)
})

test('the last sub-tab is remembered per title', async ({ page }) => {
  await page.goto('/#/drama/1/source')
  await expect(mediaTab(page, 'add')).toHaveAttribute('aria-selected', 'true')
  await selectMediaTab(page, 'tools')
  // Another title has its own tab; coming back reads the remembered one, with no ?tab= in the address.
  await page.goto('/#/drama/3/source')
  await expect(mediaTab(page, 'add')).toHaveAttribute('aria-selected', 'true')
  await selectMediaTab(page, 'details')
  await page.goto('/#/drama/1/source')
  await expect(mediaTab(page, 'tools')).toHaveAttribute('aria-selected', 'true')
  await page.goto('/#/drama/3/source')
  await expect(mediaTab(page, 'details')).toHaveAttribute('aria-selected', 'true')
})

test('the address follows the tab, so a reload stays on it', async ({ page }) => {
  await page.goto('/#/drama/1/source?tab=transcribe')
  await selectMediaTab(page, 'tools')
  await expect(page).toHaveURL(/#\/drama\/1\/source\?tab=transcribe$/)
  await page.reload()
  await expect(mediaTab(page, 'tools')).toHaveAttribute('aria-selected', 'true')
})

test('a link can open a sub-tab, and moving between tabs while open follows the link', async ({ page }) => {
  await gotoMedia(page, 'details')
  await expect(mediaPanel(page, 'details').getByRole('region', { name: 'Edit details' })).toBeVisible()
  await page.evaluate(() => { window.location.hash = '#/drama/1/source?tab=transcribe' })
  await expect(mediaTab(page, 'transcribe')).toHaveAttribute('aria-selected', 'true')
  await expect(mediaPanel(page, 'transcribe').getByRole('button', { name: 'Transcribe' })).toBeVisible()
})

test('Add media holds the upload, the transcript mode, the subtitle import and Keep original files', async ({ page }) => {
  await gotoMedia(page, 'add')
  const panel = mediaPanel(page, 'add')
  await expect(panel.getByRole('radiogroup', { name: 'Where the lines come from' })).toBeVisible()
  await expect(panel.getByRole('radiogroup', { name: 'Get audio or video' })).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Upload', exact: true })).toBeVisible()
  await expect(panel.locator('.section-title', { hasText: /^Import subtitle file$/ })).toBeVisible()
  await expect(panel.getByTestId('keep-original')).toBeVisible()
  await expect(panel.getByRole('button', { name: 'Transcribe', exact: true })).toHaveCount(0)
})

test('Transcribe holds the run form and Tools holds the rest', async ({ page }) => {
  await gotoMedia(page, 'transcribe')
  const run = mediaPanel(page, 'transcribe')
  await expect(run.getByLabel('Source language', { exact: true })).toBeVisible()
  await expect(run.getByLabel('Whisper model', { exact: true })).toBeVisible()
  await expect(run.getByText('Detect speakers', { exact: true })).toBeVisible()
  await expect(run.getByRole('button', { name: 'Transcribe', exact: true })).toBeVisible()
  await expect(run.getByRole('region', { name: 'Advanced options' })).toHaveCount(0)
  await selectMediaTab(page, 'tools')
  const tools = mediaPanel(page, 'tools')
  await expect(tools.getByRole('region', { name: 'Advanced options' })).toBeVisible()
  await expect(tools.locator('.section-title', { hasText: /^Auto-tune min silence$/ })).toBeVisible()
  await expect(tools.locator('.section-title', { hasText: /^Speech coverage$/ })).toBeVisible()
})

test('a finished run has no All jobs link', async ({ page }) => {
  const t = Math.floor(Date.now() / 1000)
  const finished = {
    job_id: 'transcribe_1', status: 'done', progress: 1, message: 'Done', started_at: t - 700, finished_at: t - 600, updated_at: t - 600,
    drama_id: 1, kind: 'transcribe', outcome: 'ok', result: { line_count: 3 },
  }
  await page.route('**/api/jobs', (route) => route.fulfill({ json: { items: [finished], count: 1 } }))
  await page.route('**/api/jobs/transcribe_1', (route) => route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No job.' } } }))
  await gotoMedia(page, 'transcribe')
  const card = page.getByTestId('last-run')
  await expect(card).toBeVisible()
  await expect(card.getByRole('link', { name: 'All jobs' })).toHaveCount(0)
})

test('the advanced options are folds; Switches opens first on a new title, and the choice is remembered', async ({ page }) => {
  await gotoMedia(page, 'tools')
  const advanced = mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })
  const folds = advanced.locator(':scope > details.section')
  await expect(folds.locator(':scope > summary > .section-title')).toHaveText(['Speakers', 'Timing', 'Models and backends', 'Switches'])
  // Never saved: Switches is the one open card.
  await expect(folds.nth(3)).toHaveAttribute('open', '')
  for (const i of [0, 1, 2]) await expect(folds.nth(i)).not.toHaveAttribute('open', '')
  await folds.nth(2).locator(':scope > summary').click()
  await folds.nth(3).locator(':scope > summary').click()
  // The fold choice is saved from the details 'toggle' event, which fires after the click returns; reloading first loses it.
  await page.waitForFunction(() => Object.keys(window.localStorage).filter((k) => k.startsWith('baihe.section.source.advanced.')).length >= 2)
  await page.reload()
  await expect(mediaTab(page, 'tools')).toHaveAttribute('aria-selected', 'true')
  const again = mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' }).locator(':scope > details.section')
  await expect(again.nth(3)).not.toHaveAttribute('open', '')
  await expect(again.nth(2)).toHaveAttribute('open', '')
})

test('every advanced control is reachable, and Developer Mode ones only with the mode on', async ({ page }) => {
  const everyday = [
    'Expected speakers', 'Min speakers', 'Max speakers', 'Sensitivity', 'Min silence', 'Pause that can split a long line',
    'Split lines by sentences', 'Realign long segments', 'Alignment method', 'ASR backend', 'Voice detector', 'Use Groq',
    'Whisper fast mode', 'Name hint for Qwen3 ASR', 'Extra names to expect', 'Separate vocals first',
  ]
  // Seeded title 1 is an audio drama: it has no picture, so the hardsub controls are not there (title 3, a video, has them).
  const developer = ['Beam size', 'VAD threshold', 'Separation backend', 'Hallucination guard', 'Whisper repeat guard']
  await gotoMedia(page, 'tools')
  const advanced = await openAdvanced(page)
  for (const label of everyday) await expect(advanced.getByLabel(label, { exact: true }), label).toBeVisible()
  await expect(advanced.getByRole('button', { name: 'Detect speakers only' })).toBeVisible()
  await expect(advanced.getByRole('button', { name: 'Save options' })).toBeVisible()
  await expect(advanced.getByRole('button', { name: 'Reset to defaults' })).toBeVisible()
  for (const label of developer) await expect(advanced.getByLabel(label, { exact: true }), label).toHaveCount(0)

  await enableDeveloperMode(page)
  await page.reload()
  const dev = await openAdvanced(page)
  for (const label of developer) await expect(dev.getByLabel(label, { exact: true }), label).toBeVisible()
})

test('a refused option on Tools is validated and shown once on the Transcribe tab', async ({ page }) => {
  await gotoMedia(page, 'tools')
  const advanced = mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })
  await openAllFolds(advanced)
  await advanced.getByLabel('Min silence', { exact: true }).fill('5')
  await advanced.getByRole('button', { name: 'Save options' }).click()
  await expect(mediaPanel(page, 'transcribe').getByRole('alert').filter({ hasText: /silence/i })).toBeVisible()
})

test('inputs in one row of the advanced options share the same top', async ({ page }) => {
  await enableDeveloperMode(page)
  await gotoMedia(page, 'tools')
  const advanced = mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })
  await openAllFolds(advanced)
  await expect(advanced.getByLabel('Pause that can split a long line', { exact: true })).toBeVisible()
  await expectRowInputsAligned(advanced)
})

test('each sub-tab fits about one desktop screen and does not scroll sideways', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  for (const tab of ['add', 'transcribe', 'details'] as const) {
    await gotoMedia(page, tab)
    await expect(mediaPanel(page, tab)).toBeVisible()
    await expectNoSidewaysScroll(page, tab)
    const height = await page.evaluate(() => document.documentElement.scrollHeight)
    // Transcribe holds Tools under it and Details holds Credits, so those two run longer.
    expect(height, `${tab}: page height`).toBeLessThanOrEqual(800 * (tab === 'add' ? 1.25 : 2.1))
  }
})

for (const scheme of ['light', 'dark'] as const) {
  test(`no sub-tab scrolls sideways at 1280x800 (${scheme})`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: scheme })
    await page.setViewportSize({ width: 1280, height: 800 })
    for (const tab of ['add', 'transcribe', 'tools', 'details', 'credits'] as const) {
      await gotoMedia(page, tab)
      await openAllFolds(mediaPanel(page, tab))
      await expectNoSidewaysScroll(page, `${tab} ${scheme}`)
    }
  })
}

test('the old Tools and Credits links land on the merged tabs with their content', async ({ page }) => {
  await page.goto('/#/drama/1/source?tab=tools')
  await expect(mediaTab(page, 'tools')).toHaveAttribute('aria-selected', 'true')
  await expect(mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })).toBeVisible()
  await page.goto('/#/drama/1/source?tab=credits')
  await expect(mediaTab(page, 'credits')).toHaveAttribute('aria-selected', 'true')
  await expect(mediaPanel(page, 'credits').getByRole('region', { name: 'Credits' })).toBeVisible()
})
