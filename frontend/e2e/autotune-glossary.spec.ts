import { expect, test, type Page, type Route } from '@playwright/test'

// Auto-tune (Transcribe > Advanced), Glossary > From novel, and the PC-only
// stage deletes. Drama reads hit the real seeded API; the auto-tune and
// glossary jobs and the delete routes are mocked; /api/meta is mocked only
// for remote mode (the real API reports local: true on loopback)
// (no GPU, no paid engine, and deletes must not touch the shared library).

test.use({ viewport: { width: 1280, height: 800 } })

const SHOTS = process.env.SHOT_DIR

// Some mocks proxy to the real API (route.fetch); let in-flight ones drop.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function shot(page: Page, name: string) {
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true })
}

async function openSection(page: Page, title: string) {
  await page.locator('.section-title', { hasText: new RegExp(`^${title}$`) }).first().click()
}

async function mockRemote(page: Page) {
  await page.route('**/api/meta', (route) =>
    route.fulfill({
      json: { app: 'baihe', api_version: '1', environment: 'development', local: false },
    }),
  )
}

async function withAudio(page: Page) {
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }),
  )
}

// The real drama, reported as belonging to series 7.
async function inSeries(page: Page) {
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
}

const done = (results: { candidate_ms: number; long_lines: number; total_lines: number }[]) => ({
  job_id: 'autotune_1', status: 'done', progress: 1, message: '', results, best_candidate_ms: 800,
})

test.describe('Auto-tune min silence', () => {
  test('is disabled with its reason when the drama has no audio', async ({ page }) => {
    await page.goto('/#/drama/1/source')
    await openSection(page, 'Advanced')
    await openSection(page, 'Auto-tune min silence')
    await expect(page.getByRole('button', { name: 'Start auto-tune' })).toBeDisabled()
    await expect(page.getByTestId('autotune')).toContainText('Still needed: audio on this drama.')
  })

  test('runs, shows progress with Cancel, then results, and Use posts candidate_ms', async ({ page }) => {
    await withAudio(page)
    let state: 'none' | 'running' | 'done' = 'none'
    let startBody = ''
    let applyBody = ''
    await page.route('**/api/transcribe/dramas/1/autotune', (route: Route) => {
      if (route.request().method() === 'POST') {
        startBody = route.request().postData() ?? ''
        state = 'running'
        return route.fulfill({ json: { job_id: 'autotune_1', candidates: [300, 800, 1500] } })
      }
      if (state === 'none') return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'none' } } })
      if (state === 'running') {
        return route.fulfill({
          json: { job_id: 'autotune_1', status: 'running', progress: 0.33, message: 'Testing candidate 2 of 3 (800ms)...', results: null, best_candidate_ms: null },
        })
      }
      return route.fulfill({
        json: done([
          { candidate_ms: 300, long_lines: 9, total_lines: 120 },
          { candidate_ms: 800, long_lines: 2, total_lines: 96 },
          { candidate_ms: 1500, long_lines: 5, total_lines: 70 },
        ]),
      })
    })
    await page.route('**/api/transcribe/dramas/1/autotune/apply', async (route) => {
      applyBody = route.request().postData() ?? ''
      const cfg = await (await page.request.get('/api/transcribe/dramas/1/config')).json()
      await route.fulfill({ json: { ...cfg, min_silence_ms: 800 } })
    })
    let cancelled = false
    await page.route('**/api/jobs/autotune_1/cancel', (route) => {
      cancelled = true
      return route.fulfill({ json: { job_id: 'autotune_1', cancel_requested: true, status: 'running' } })
    })

    await page.goto('/#/drama/1/source')
    await openSection(page, 'Advanced')
    await openSection(page, 'Auto-tune min silence')
    const start = page.getByRole('button', { name: 'Start auto-tune' })
    await expect(start).toBeEnabled()
    await expect(start).not.toHaveClass(/primary/)
    await start.click()
    await expect(page.getByTestId('autotune-running')).toContainText('Testing 2 of 3 (800 ms)…')
    await page.getByTestId('autotune-running').getByRole('button', { name: 'Cancel' }).click()
    await expect.poll(() => cancelled).toBe(true)
    expect(JSON.parse(startBody)).toEqual({})

    state = 'done'
    const table = page.getByTestId('autotune-results')
    await expect(table.locator('tbody tr')).toHaveCount(3)
    await expect(table.locator('tbody tr').nth(1)).toContainText('Fewest long lines')
    await expect(page.getByText('Fewest long lines')).toHaveCount(1)
    await shot(page, 'autotune-desktop')
    await page.emulateMedia({ colorScheme: 'dark' })
    await shot(page, 'autotune-desktop-dark')
    await page.emulateMedia({ colorScheme: 'light' })
    await table.getByRole('button', { name: 'Use 800 ms' }).click()
    await expect(page.getByTestId('autotune')).toContainText('Min silence set to 800 ms. Transcribe again to apply.')
    expect(JSON.parse(applyBody)).toEqual({ candidate_ms: 800 })
    await expect(page.getByRole('spinbutton', { name: 'Min silence' })).toHaveValue('800')
  })

  test('a lost run (400 on apply) asks to run again', async ({ page }) => {
    await withAudio(page)
    await page.route('**/api/transcribe/dramas/1/autotune', (route) =>
      route.fulfill({ json: done([{ candidate_ms: 800, long_lines: 1, total_lines: 10 }]) }),
    )
    await page.route('**/api/transcribe/dramas/1/autotune/apply', (route) =>
      route.fulfill({ status: 400, json: { error: { code: 'unsupported_operation', message: 'No finished auto-tune results for this drama.' } } }),
    )
    await page.goto('/#/drama/1/source')
    await openSection(page, 'Advanced')
    await openSection(page, 'Auto-tune min silence')
    await page.getByRole('button', { name: 'Use 800 ms' }).click()
    await expect(page.getByRole('alert').filter({ hasText: 'Run auto-tune again (results are kept only until the app restarts).' })).toBeVisible()
  })
})

test.describe('Glossary from novel', () => {
  test('is disabled without a series, with a link to Details', async ({ page }) => {
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Glossary')
    await openSection(page, 'From novel')
    const box = page.getByTestId('novel-glossary')
    await expect(box.getByRole('button', { name: 'Extract terms' })).toBeDisabled()
    await expect(box).toContainText('Still needed: a series for this drama')
    await expect(box.getByRole('link', { name: 'set it in Details' })).toHaveAttribute('href', '#/drama/1/source')
  })

  test('a paid-engine 403 shows the paid copy', async ({ page }) => {
    await inSeries(page)
    await page.route('**/api/novel/dramas/1/status', (route) =>
      route.fulfill({ json: { drama_id: 1, has_novel_text: true, char_count: 900, chapters: 3, ocr_running: false } }),
    )
    await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/glossary/dramas/1/from-novel', (route) =>
      route.request().method() === 'POST'
        ? route.fulfill({ status: 403, json: { error: { code: 'forbidden', message: 'Not allowed.' } } })
        : route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'none' } } }),
    )
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Glossary')
    await openSection(page, 'From novel')
    await page.getByRole('button', { name: 'Extract terms' }).click()
    await expect(page.getByRole('alert').filter({ hasText: "This engine is paid and this account can't use it." })).toBeVisible()
  })

  test('extracts, shows proposals, and adds only the checked terms', async ({ page }) => {
    await inSeries(page)
    await page.route('**/api/novel/dramas/1/status', (route) =>
      route.fulfill({ json: { drama_id: 1, has_novel_text: true, char_count: 900, chapters: 3, ocr_running: false } }),
    )
    await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
    let state: 'none' | 'running' | 'done' = 'none'
    await page.route('**/api/glossary/dramas/1/from-novel', (route) => {
      if (route.request().method() === 'POST') {
        state = 'running'
        return route.fulfill({ json: { job_id: 'novelglossary_1', engine: 'claude', paired: false } })
      }
      if (state === 'none') return route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'none' } } })
      if (state === 'running') {
        return route.fulfill({ json: { job_id: 'novelglossary_1', status: 'running', progress: 0.42, message: '', proposals: null } })
      }
      const prop = (term: string, en: string, already = false) => ({
        term, suggested_translation: en, category: 'person', policy: 'keep', reason: 'Recurring name', already_in_glossary: already,
      })
      return route.fulfill({
        json: {
          job_id: 'novelglossary_1', status: 'done', progress: 1, message: '',
          proposals: [prop('魏婴', 'Wei Ying'), prop('蓝湛', 'Lan Zhan'), prop('云深不知处', 'Cloud Recesses'), prop('江澄', 'Jiang Cheng', true)],
        },
      })
    })
    let applyBody = ''
    await page.route('**/api/glossary/dramas/1/from-novel/apply', (route) => {
      applyBody = route.request().postData() ?? ''
      return route.fulfill({ json: { added: ['魏婴', '云深不知处'], overwritten: [], skipped_existing: [], unknown: [] } })
    })

    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Glossary')
    await openSection(page, 'From novel')
    await page.getByRole('button', { name: 'Extract terms' }).click()
    await expect(page.getByTestId('novel-glossary-running')).toContainText('Reading the novel… 42%')
    await expect(page.getByTestId('novel-glossary-running').getByRole('button', { name: 'Cancel' })).toBeVisible()
    state = 'done'
    const table = page.getByTestId('novel-glossary-proposals')
    await expect(table.locator('tbody tr')).toHaveCount(4)
    await expect(table.locator('tbody tr').nth(3)).toContainText('already in glossary')
    const add = page.getByRole('button', { name: 'Add 3 terms to series glossary' })
    await expect(add).toBeVisible()
    await page.getByLabel('Select 蓝湛').uncheck()
    await shot(page, 'glossary-from-novel-desktop')
    await page.emulateMedia({ colorScheme: 'dark' })
    await shot(page, 'glossary-from-novel-desktop-dark')
    await page.emulateMedia({ colorScheme: 'light' })
    await page.getByRole('button', { name: 'Add 2 terms to series glossary' }).click()
    await expect(page.getByTestId('novel-glossary')).toContainText('Added 2.')
    expect(JSON.parse(applyBody)).toEqual({ terms: ['魏婴', '云深不知处'] })
    await expect(page.getByTestId('novel-glossary').locator('button.primary')).toHaveCount(1)
  })

  test('overwriting always confirms, counts against the current glossary and sends confirm: true', async ({ page }) => {
    await inSeries(page)
    await page.route('**/api/glossary/dramas/1/terms', (route) =>
      route.fulfill({
        json: [{ id: 1, term_original: '江澄', term_translation: 'Jiang Cheng', notes: '', category: null, policy: null, enforce_exact: false, aliases: [], banned_translations: [] }],
      }),
    )
    await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/glossary/dramas/1/from-novel', (route) =>
      route.fulfill({
        json: {
          job_id: 'novelglossary_1', status: 'done', progress: 1, message: '',
          proposals: [{ term: '江澄', suggested_translation: 'Jiang Cheng', category: null, policy: null, reason: '', already_in_glossary: true }],
        },
      }),
    )
    let applyBody = ''
    await page.route('**/api/glossary/dramas/1/from-novel/apply', (route) => {
      applyBody = route.request().postData() ?? ''
      return route.fulfill({ json: { added: [], overwritten: ['江澄'], skipped_existing: [], unknown: [] } })
    })
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Glossary')
    await openSection(page, 'From novel')
    await page.getByTestId('novel-glossary-proposals').getByLabel('Select 江澄').check()
    await page.getByLabel('Overwrite existing terms').check()
    await expect(page.getByText('Tick terms marked "already in glossary" to replace them.')).toBeVisible()
    await page.getByRole('button', { name: 'Add 1 term to series glossary' }).click()
    await expect(page.getByText('Replace 1 existing term in the series glossary?')).toBeVisible()
    expect(applyBody).toBe('')
    await page.getByRole('button', { name: 'Yes, overwrite' }).click()
    await expect(page.getByTestId('novel-glossary')).toContainText('Overwrote 1.')
    expect(JSON.parse(applyBody)).toEqual({ terms: ['江澄'], overwrite_existing: true, confirm: true })
  })

  test('a lost extraction (400 on apply) asks to run again', async ({ page }) => {
    await inSeries(page)
    await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/glossary/dramas/1/from-novel', (route) =>
      route.fulfill({
        json: {
          job_id: 'novelglossary_1', status: 'done', progress: 1, message: '',
          proposals: [{ term: '魏婴', suggested_translation: 'Wei Ying', category: null, policy: null, reason: '', already_in_glossary: false }],
        },
      }),
    )
    await page.route('**/api/glossary/dramas/1/from-novel/apply', (route) =>
      route.fulfill({ status: 400, json: { error: { code: 'unsupported_operation', message: 'No finished glossary extraction for this drama.' } } }),
    )
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Glossary')
    await openSection(page, 'From novel')
    await page.getByRole('button', { name: 'Add 1 term to series glossary' }).click()
    await expect(page.getByRole('alert').filter({ hasText: 'Run the extraction again (results are kept only until the app restarts).' })).toBeVisible()
  })
})

test.describe('PC-only stage deletes', () => {
  const version = { id: 9, drama_id: 1, label: 'Claude pass 1', engine: 'claude', model: 'm', is_active: true, created_at: '2026-09-01' }

  const version2 = { ...version, id: 10, label: 'Claude pass 2', is_active: false }

  async function mockVersions(page: Page) {
    let versions = [version, version2]
    const calls: { body: string; local: string | null }[] = []
    await page.route('**/api/review/dramas/1/versions', (route) => route.fulfill({ json: versions }))
    await page.route('**/api/review/dramas/1/versions/9/delete', (route) => {
      calls.push({ body: route.request().postData() ?? '', local: route.request().headers()['x-baihe-local'] ?? null })
      versions = [version2]
      return route.fulfill({ json: { drama_id: 1, version_id: 9, deleted: true, was_active: true } })
    })
    return calls
  }

  test('version delete: first tap makes no call, the second sends confirm with the local header', async ({ page }) => {
    const calls = await mockVersions(page)
    await page.goto('/#/drama/1/review')
    await openSection(page, 'Records')
    const list = page.getByTestId('versions-list')
    await list.getByRole('button', { name: 'Delete Claude pass 1', exact: true }).click()
    expect(calls).toHaveLength(0)
    await expect(list).toContainText('Press again to delete Claude pass 1')
    await shot(page, 'records-version-delete-armed-desktop')
    await list.getByRole('button', { name: 'Confirm delete Claude pass 1' }).click()
    await expect(list.locator('li')).toHaveCount(1)
    // Focus moves to the next row's Delete button.
    await expect(list.getByRole('button', { name: 'Delete Claude pass 2', exact: true })).toBeFocused()
    expect(calls).toHaveLength(1)
    expect(JSON.parse(calls[0].body)).toEqual({ confirm: true })
    expect(calls[0].local).toBe('1')
  })

  test('Escape cancels and the button reverts after 5 s', async ({ page }) => {
    const calls = await mockVersions(page)
    await page.goto('/#/drama/1/review')
    await openSection(page, 'Records')
    const list = page.getByTestId('versions-list')
    await list.getByRole('button', { name: 'Delete Claude pass 1', exact: true }).click()
    await page.keyboard.press('Escape')
    await expect(list.getByRole('button', { name: 'Delete Claude pass 1', exact: true })).toBeVisible()
    await list.getByRole('button', { name: 'Delete Claude pass 1', exact: true }).click()
    await expect(list.getByRole('button', { name: 'Confirm delete Claude pass 1' })).toBeVisible()
    await expect(list.getByRole('button', { name: 'Delete Claude pass 1', exact: true })).toBeVisible({ timeout: 7000 })
    expect(calls).toHaveLength(0)
  })

  test('remote (meta local: false): no delete buttons, one muted note', async ({ page }) => {
    await mockRemote(page)
    await mockVersions(page)
    await withAudio(page)
    await page.goto('/#/drama/1/review')
    await openSection(page, 'Records')
    await expect(page.getByTestId('versions-list')).toBeVisible()
    await expect(page.getByRole('button', { name: /Delete Claude pass 1/ })).toHaveCount(0)
    await expect(page.getByText('Deleting is PC only.')).toBeVisible()
    await page.goto('/#/drama/1/source')
    await expect(page.getByTestId('media-status')).toContainText('Audio: attached')
    await expect(page.getByRole('button', { name: /Remove audio\/video/ })).toHaveCount(0)
  })

  test('remove audio/video and raw novel on the PC', async ({ page }) => {
    await withAudio(page)
    // The raw novel is shown and removed by NovelFilePanel (Transcribe).
    let rawPresent = true
    await page.route('**/api/novel/dramas/1/raw-novel', (route) =>
      route.fulfill({ json: { drama_id: 1, present: rawPresent, size_bytes: rawPresent ? 9 : 0, char_count: rawPresent ? 3 : 0 } }))
    const posted: string[] = []
    await page.route('**/api/media/dramas/1/remove', (route) => {
      posted.push(route.request().url())
      return route.fulfill({ json: { drama_id: 1, removed: true, audio_file_removed: true, video_file_removed: false, has_audio: false, has_video: false } })
    })
    await page.route('**/api/novel/dramas/1/raw-novel/remove', (route) => {
      posted.push(route.request().url())
      rawPresent = false
      return route.fulfill({ json: { drama_id: 1, removed: true, has_raw_novel_context: false } })
    })
    await page.goto('/#/drama/1/source')
    await page.getByRole('button', { name: 'Remove audio/video', exact: true }).click()
    await shot(page, 'source-remove-media-armed-desktop')
    await page.getByRole('button', { name: 'Confirm remove audio/video' }).click()
    await expect(page.getByText('Removed. Lines are untouched.')).toBeVisible()
    await openSection(page, 'Novel text')
    await expect(page.getByRole('link', { name: 'Build a glossary from this novel (Translate → Glossary) →' })).toHaveAttribute('href', '#/drama/1/translate')
    const raw = page.getByRole('region', { name: 'Raw novel (original language)' })
    await raw.locator('.section-title').click()
    await raw.getByRole('button', { name: 'Remove raw novel', exact: true }).click()
    await raw.getByRole('button', { name: 'Confirm remove raw novel' }).click()
    await expect(raw.getByRole('status')).toHaveText('Removed.')
    await expect(page.getByRole('link', { name: /Build a glossary from this novel/ })).toHaveCount(0)
    expect(posted.map((u) => new URL(u).pathname)).toEqual([
      '/api/media/dramas/1/remove',
      '/api/novel/dramas/1/raw-novel/remove',
    ])
  })

  test('series cast lists characters and removes one', async ({ page }) => {
    await inSeries(page)
    await page.route('**/api/characters/series/7/characters', (route) =>
      route.fulfill({
        json: [
          { id: 11, character_name: 'Wei Ying', aliases: 'Wei Wuxian', notes: '', pronouns: 'he/him' },
          { id: 12, character_name: 'Lan Zhan', aliases: '', notes: '', pronouns: 'he/him' },
        ],
      }),
    )
    let body = ''
    await page.route('**/api/characters/series/7/characters/11/delete', (route) => {
      body = route.request().postData() ?? ''
      return route.fulfill({ json: { series_id: 7, character_id: 11, deleted: true } })
    })
    await page.goto('/#/drama/1/translate')
    await openSection(page, 'Characters')
    await openSection(page, 'Series cast')
    const cast = page.getByTestId('series-cast')
    await expect(cast.locator('li')).toHaveCount(2)
    await shot(page, 'series-cast-desktop')
    await cast.getByRole('button', { name: 'Remove Wei Ying', exact: true }).click()
    await cast.getByRole('button', { name: 'Confirm remove Wei Ying from series' }).click()
    await expect(cast.locator('li')).toHaveCount(1)
    expect(JSON.parse(body)).toEqual({ confirm: true })
  })
})
