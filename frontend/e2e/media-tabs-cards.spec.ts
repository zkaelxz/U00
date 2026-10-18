import { expect, test, type Page } from '@playwright/test'

import { gotoMedia, mediaPanel, mediaTab, topCards, selectMediaTab } from './mediaTabs'
import { enableDeveloperMode, openAdvanced } from './sourceHelpers'

// The cards inside each Media sub-tab: the one-open-card fold rule, the Switches card, the prompt fold, and
// the Details and Credits tabs sharing one draft. Seeded title 1 is an audio drama.

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

// Reads come from the seeded API; saves are echoed back so the shared library is not changed.
async function echoConfig(page: Page, patch: Record<string, unknown> = {}) {
  const saves: Record<string, unknown>[] = []
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch({ method: 'GET' })
    const cfg = { ...(await resp.json()), ...patch }
    if (route.request().method() !== 'POST') return route.fulfill({ response: resp, json: cfg })
    const body = route.request().postDataJSON() as Record<string, unknown>
    saves.push(body)
    return route.fulfill({ response: resp, json: { ...cfg, ...body } })
  })
  return saves
}

async function noNovel(page: Page) {
  await page.route('**/api/novel/dramas/1/status', (r) => r.fulfill({ json: { drama_id: 1, has_novel_text: false, char_count: 0, chapters: 0, ocr_running: false } }))
  await page.route('**/api/novel/dramas/1/raw-novel', (r) => r.fulfill({ json: { drama_id: 1, present: false, size_bytes: 0, char_count: 0 } }))
}

const fold = (panel: ReturnType<typeof mediaPanel>, title: string) =>
  panel.locator('details.section', { has: panel.page().locator(`summary > .section-title`, { hasText: new RegExp(`^${title}$`) }) }).first()

test.describe('one open card per sub-tab', () => {
  test('fresh storage opens exactly one card on every tab that has several', async ({ page }) => {
    await noNovel(page)
    await echoConfig(page)
    for (const tab of ['add', 'tools', 'credits'] as const) {
      await gotoMedia(page, tab)
      const panel = mediaPanel(page, tab)
      if (tab === 'tools') await expect(panel.getByRole('region', { name: 'Advanced options' })).toBeVisible()
      const cards = await topCards(panel)
      expect(cards.length, `${tab}: cards`).toBeGreaterThan(1)
      expect(cards.filter((c) => c.open), `${tab}: open cards`).toHaveLength(1)
    }
    // Details has one card, and it is open.
    await gotoMedia(page, 'details')
    expect(await topCards(mediaPanel(page, 'details'))).toEqual([{ title: 'Edit details', open: true }])
  })

  test('the first card is the one that opens: Import subtitle file, Credits, Switches', async ({ page }) => {
    await noNovel(page)
    await echoConfig(page)
    await gotoMedia(page, 'add')
    expect((await topCards(mediaPanel(page, 'add'))).find((c) => c.open)?.title).toBe('Import subtitle file')
    await gotoMedia(page, 'credits')
    expect((await topCards(mediaPanel(page, 'credits'))).find((c) => c.open)?.title).toBe('Credits')
    await gotoMedia(page, 'tools')
    await expect(mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })).toBeVisible()
    expect((await topCards(mediaPanel(page, 'tools'))).find((c) => c.open)?.title).toBe('Switches')
  })

  test('what the viewer folds and unfolds is kept', async ({ page }) => {
    await noNovel(page)
    await gotoMedia(page, 'credits')
    const panel = mediaPanel(page, 'credits')
    await fold(panel, 'Cover and romanized credits').locator(':scope > summary').click()
    await fold(panel, 'Credits').locator(':scope > summary').click()
    await expect(fold(panel, 'Cover and romanized credits')).toHaveAttribute('open', '')
    await expect(fold(panel, 'Credits')).not.toHaveAttribute('open', '')
    // The fold choice is saved from the details 'toggle' event, which fires after the click returns; reloading first loses it.
    await page.waitForFunction(() => ['source.cover', 'source.credits'].every((k) => window.localStorage.getItem(`baihe.section.${k}`) !== null))
    await page.reload()
    const again = mediaPanel(page, 'credits')
    await expect(fold(again, 'Cover and romanized credits')).toHaveAttribute('open', '')
    await expect(fold(again, 'Credits')).not.toHaveAttribute('open', '')
  })
})

test.describe('Switches card', () => {
  const folded = async (page: Page) => {
    await page.addInitScript(() => localStorage.setItem('baihe.section.source.advanced.switches', '0'))
  }
  const summaryOf = async (page: Page) => {
    await gotoMedia(page, 'tools')
    const card = fold(mediaPanel(page, 'tools'), 'Switches')
    await expect(card).toBeVisible()
    return card.locator(':scope > summary .section-summary')
  }

  test('says All off with nothing on', async ({ page }) => {
    await echoConfig(page, { use_groq: false, separate_vocals_first: false, realign_long_segments: false, whisper_fast_mode: false, split_by_sentences: false, vocabulary_hint: false, whisper_repeat_guard: false })
    await folded(page)
    await expect(await summaryOf(page)).toHaveText('All off')
  })

  test('names the one that is on', async ({ page }) => {
    await echoConfig(page, { use_groq: true, separate_vocals_first: false, realign_long_segments: false, whisper_fast_mode: false, split_by_sentences: false, vocabulary_hint: false })
    await folded(page)
    await expect(await summaryOf(page)).toHaveText('1 on: Use Groq')
  })

  test('names three in the card order', async ({ page }) => {
    await echoConfig(page, { use_groq: true, separate_vocals_first: true, split_by_sentences: true, realign_long_segments: false, whisper_fast_mode: false, vocabulary_hint: false })
    await folded(page)
    await expect(await summaryOf(page)).toHaveText('3 on: Separate vocals first, Split lines by sentences, Use Groq')
  })

  test('is open on a title whose options were never saved, closed after Save options, and the viewer wins after that', async ({ page }) => {
    await echoConfig(page)
    await gotoMedia(page, 'tools')
    const advanced = mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })
    await expect(advanced).toBeVisible()
    await expect(fold(mediaPanel(page, 'tools'), 'Switches')).toHaveAttribute('open', '')
    await advanced.getByRole('button', { name: 'Save options' }).click()
    await expect(advanced.getByRole('status').filter({ hasText: 'Saved.' })).toBeVisible()
    await page.reload()
    await expect(mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })).toBeVisible()
    await expect(fold(mediaPanel(page, 'tools'), 'Switches')).not.toHaveAttribute('open', '')
    await expect(fold(mediaPanel(page, 'tools'), 'Speakers')).toHaveAttribute('open', '')
    // The viewer opens it; that is remembered.
    await fold(mediaPanel(page, 'tools'), 'Switches').locator(':scope > summary').click()
    await expect.poll(() => page.evaluate(() => localStorage.getItem('baihe.section.source.advanced.switches'))).toBe('1')
    await page.reload()
    await expect(mediaPanel(page, 'tools').getByRole('region', { name: 'Advanced options' })).toBeVisible()
    await expect(fold(mediaPanel(page, 'tools'), 'Switches')).toHaveAttribute('open', '')
  })

  test('the switches still save', async ({ page }) => {
    const saves = await echoConfig(page)
    await gotoMedia(page, 'tools')
    const advanced = await openAdvanced(page)
    await advanced.getByRole('switch', { name: 'Use Groq' }).click()
    await advanced.getByRole('switch', { name: 'Separate vocals first' }).click()
    await advanced.getByRole('button', { name: 'Save options' }).click()
    await expect.poll(() => saves.length).toBe(1)
    expect(saves[0]).toMatchObject({ use_groq: true, separate_vocals_first: true })
  })

  test('holds the Developer Mode switch only with the mode on', async ({ page }) => {
    await gotoMedia(page, 'tools')
    const advanced = await openAdvanced(page)
    await expect(advanced.getByRole('switch', { name: 'Whisper repeat guard' })).toHaveCount(0)
    await enableDeveloperMode(page)
    await page.reload()
    const dev = await openAdvanced(page)
    await expect(dev.getByRole('switch', { name: 'Whisper repeat guard' })).toBeVisible()
  })
})

test.describe('prompt fold', () => {
  test('Extra names sit inside the fold with the replacement prompt; the summary names what is set', async ({ page }) => {
    await echoConfig(page)
    await gotoMedia(page, 'tools')
    const advanced = await openAdvanced(page, false)
    // The automatic-prompt line stays above the fold.
    await expect(advanced.getByTestId('auto-prompt')).toBeVisible()
    const names = advanced.getByLabel('Extra names to expect', { exact: true })
    await expect(names).toBeHidden()
    await expect(advanced.getByTestId('prompt-summary')).toHaveCount(0)

    const more = advanced.locator('summary', { hasText: 'More: replace the automatic prompt' })
    await more.click()
    await expect(names).toBeVisible()
    await expect(advanced.getByLabel('Replacement prompt', { exact: true })).toBeVisible()
    await names.fill('沈清疑、云隐宗')
    await advanced.getByLabel('Replacement prompt', { exact: true }).fill('full prompt')
    await more.click()
    await expect(advanced.getByTestId('prompt-summary')).toHaveText('Extra names: 2 · custom prompt set')
  })

  test('the names are still saved with the draft and sent with the run', async ({ page }) => {
    await echoConfig(page)
    const bodies: Record<string, unknown>[] = []
    await page.route('**/api/transcribe/dramas/1/run', (route) => {
      bodies.push(route.request().postDataJSON() as Record<string, unknown>)
      return route.fulfill({ json: { job_id: 'fake-job' } })
    })
    await page.route('**/api/jobs/fake-job', (route) =>
      route.fulfill({ json: { job_id: 'fake-job', status: 'running', progress: 0.1, message: 'x', error: null, description: null, gpu_touching: true, started_at: 1, finished_at: null, updated_at: 1 } }))
    await gotoMedia(page, 'tools')
    const advanced = await openAdvanced(page)
    await advanced.getByLabel('Extra names to expect', { exact: true }).fill('沈清疑、云隐宗')
    await page.reload()
    const again = await openAdvanced(page)
    await expect(again.getByLabel('Extra names to expect', { exact: true })).toHaveValue('沈清疑、云隐宗')
    await selectMediaTab(page, 'transcribe')
    const transcript = page.getByLabel('Transcript text', { exact: true })
    if (await transcript.count()) await transcript.fill('line one')
    await page.getByRole('button', { name: 'Transcribe', exact: true }).click()
    await expect.poll(() => bodies.length).toBe(1)
    expect(bodies[0]).toMatchObject({ extra_names: '沈清疑、云隐宗' })
  })
})

test.describe('range hints', () => {
  const hint = async (page: Page, label: string) => {
    const advanced = await openAdvanced(page)
    await advanced.getByRole('button', { name: `Help: ${label}` }).focus()
    return page.getByRole('tooltip').filter({ hasText: /./ }).first()
  }

  test('the pause field says 0.1-2.0 s with its default', async ({ page }) => {
    await gotoMedia(page, 'tools')
    await expect(await hint(page, 'Pause that can split a long line')).toContainText('0.1-2.0 s, default 0.35 s.')
  })

  test('min silence says its range and default in ms', async ({ page }) => {
    await gotoMedia(page, 'tools')
    await expect(await hint(page, 'Min silence')).toContainText('100-3000 ms, default 300 ms.')
  })

  test('speaker counts read as whole-number ranges', async ({ page }) => {
    await gotoMedia(page, 'tools')
    await expect(await hint(page, 'Expected speakers')).toContainText('0-20.')
  })
})

test.describe('Details and Credits share one draft', () => {
  test('each field is on its own tab', async ({ page }) => {
    await gotoMedia(page, 'details')
    const details = mediaPanel(page, 'details')
    for (const l of ['English title', 'Original title', 'Source language', 'Media type', 'Series', 'Publication status', 'Episode number', 'Summary']) {
      await expect(details.getByLabel(l, { exact: true }), l).toBeVisible()
    }
    await expect(details.getByLabel('Author', { exact: true })).toHaveCount(0)
    await expect(details.getByLabel('Genre', { exact: true })).toHaveCount(0)
    await expect(details.getByRole('button', { name: 'Save details' })).toBeVisible()
    await selectMediaTab(page, 'credits')
    const credits = mediaPanel(page, 'credits')
    for (const l of ['Author', 'Studio', 'Director', 'Voice actors', 'Genre']) await expect(credits.getByLabel(l, { exact: true }), l).toBeVisible()
    await expect(credits.getByLabel('Summary', { exact: true })).toHaveCount(0)
    await expect(credits.getByRole('button', { name: 'Save credits' })).toBeVisible()
    await expect(credits.getByRole('region', { name: 'Fill in details' })).toBeVisible()
    await fold(credits, 'Cover and romanized credits').locator(':scope > summary').click()
    await expect(credits.getByLabel('Cover image', { exact: true })).toBeVisible()
  })

  test('a draft survives switching tabs, shows a dot on its tab, and one Save persists both', async ({ page }) => {
    const sent: Record<string, unknown>[] = []
    const drama = await (await page.request.get('/api/library/dramas/1')).json()
    await page.route('**/api/dramas/1/metadata', (r) => {
      sent.push(r.request().postDataJSON() as Record<string, unknown>)
      return r.fulfill({ json: drama })
    })
    await gotoMedia(page, 'details')
    await mediaPanel(page, 'details').getByLabel('Summary', { exact: true }).fill('A new summary')
    await expect(page.getByTestId('tab-unsaved-details')).toBeVisible()

    // Credits sits under Details on the same tab, and the draft is one form.
    await mediaPanel(page, 'credits').getByLabel('Genre', { exact: true }).fill('xianxia')
    await expect(page.getByTestId('tab-unsaved-details')).toBeVisible()

    // Away and back, the draft is still there, with no discard prompt.
    await selectMediaTab(page, 'add')
    await selectMediaTab(page, 'details')
    await expect(mediaPanel(page, 'details').getByLabel('Summary', { exact: true })).toHaveValue('A new summary')
    await expect(mediaPanel(page, 'credits').getByLabel('Genre', { exact: true })).toHaveValue('xianxia')

    // Saving from Credits saves the whole form.
    await mediaPanel(page, 'credits').getByRole('button', { name: 'Save credits' }).click()
    await expect.poll(() => sent.length).toBe(1)
    expect(sent[0]).toMatchObject({ summary: 'A new summary', genre: 'xianxia' })
    await expect(page.getByTestId('tab-unsaved-details')).toHaveCount(0)
  })

  test('Save details on the Details tab saves a credits edit too', async ({ page }) => {
    const sent: Record<string, unknown>[] = []
    const drama = await (await page.request.get('/api/library/dramas/1')).json()
    await page.route('**/api/dramas/1/metadata', (r) => {
      sent.push(r.request().postDataJSON() as Record<string, unknown>)
      return r.fulfill({ json: drama })
    })
    await gotoMedia(page, 'credits')
    await mediaPanel(page, 'credits').getByLabel('Studio', { exact: true }).fill('A studio')
    await mediaPanel(page, 'details').getByRole('button', { name: 'Save details' }).click()
    await expect.poll(() => sent.length).toBe(1)
    expect(sent[0]).toMatchObject({ studio: 'A studio' })
  })

  test('an error in the details shows when saving from Credits', async ({ page }) => {
    await gotoMedia(page, 'details')
    await mediaPanel(page, 'details').getByLabel('Episode number', { exact: true }).fill('abc')
    await mediaPanel(page, 'credits').getByLabel('Genre', { exact: true }).fill('x')
    await mediaPanel(page, 'credits').getByRole('button', { name: 'Save credits' }).click()
    await expect(mediaPanel(page, 'details').getByRole('alert').filter({ hasText: /whole number/i })).toBeVisible()
  })

  test('Details with Credits under it fits about one and a half desktop screens', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 })
    await gotoMedia(page, 'details')
    const height = await page.evaluate(() => document.documentElement.scrollHeight)
    expect(height, 'page height').toBeLessThanOrEqual(800 * 1.75)
  })
})
