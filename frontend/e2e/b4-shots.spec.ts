import { openTranscribeOptions } from './sourceHelpers'
import { test, type Page } from '@playwright/test'

// Review screenshots for the B4 Workspace stage gaps (P05, P10, P11/X09,
// D03/D04/D06, X16, X24, U01). Skipped unless B4_SHOTS_DIR=<dir> is set.
// Every write is mocked or aborted; reads that matter are mocked with a
// realistic state. The same file runs on the base commit for the "before"
// shots, so it only waits for things both versions have.

const DIR = process.env.B4_SHOTS_DIR
test.skip(!DIR, 'set B4_SHOTS_DIR to save screenshots')

async function common(page: Page) {
  // Nothing is ever written from here.
  await page.route('**/api/dramas/*/metadata', (r) => r.abort())
  await page.route('**/api/*/dramas/*/run**', (r) => r.abort())
  await page.route('**/api/characters/series/*/characters/*', (r) =>
    r.request().method() === 'GET' ? r.fallback() : r.abort())
}

async function drama(page: Page, id: number, patch: Record<string, unknown>) {
  const real = await (await page.request.get(`/api/library/dramas/${id}`)).json()
  await page.route(`**/api/library/dramas/${id}`, (r) => r.fulfill({ json: { ...real, ...patch } }))
}

async function media(page: Page, id: number, video: boolean) {
  await page.route(`**/api/media/dramas/${id}/status`, (r) =>
    r.fulfill({ json: { drama_id: id, has_audio: true, has_source_video: video, upload_max_mb: 2048 } }))
  await page.route(`**/api/metadata/dramas/${id}/analyze-media`, (r) =>
    r.fulfill({
      json: {
        drama_id: id, duration_seconds: 2712, has_video: video, has_audio: true, audio_track_count: 1, sample_rate: 48000,
        width: video ? 1920 : null, height: video ? 1080 : null, fps: video ? 29.97 : null,
        subtitle_tracks: video ? [{ index: 2, codec: 'ass', language: 'chi' }] : [],
        suggested_pipeline: video
          ? ['Import existing subtitle track (chi) instead of transcribing', 'Translate', 'Export subtitles (ASS/VTT/SRT)']
          : ['Transcribe (Whisper)', 'Diarize speakers', 'Translate', 'Export subtitles (ASS/VTT/SRT)'],
        content_type_guess: video ? 'video_drama' : 'audio_drama',
        content_type_reason: video ? 'has a video track' : 'long, single-track audio file',
      },
    }))
}

async function openSection(page: Page, title: string, timeout?: number) {
  // Some sections now start open; click only a closed one.
  const summary = page.locator('summary').filter({ has: page.locator('.section-title', { hasText: new RegExp(`^${title}`) }) }).first()
  if ((await summary.locator('xpath=..').getAttribute('open', { timeout })) === null) await summary.click({ timeout })
}

async function shot(page: Page, name: string, selector?: string) {
  await page.waitForTimeout(300)
  const target = selector ? page.locator(selector).first() : null
  if (target) await target.screenshot({ path: `${DIR}/${name}.png` })
  else await page.screenshot({ path: `${DIR}/${name}.png`, fullPage: true })
}

for (const vp of [
  { tag: 'desktop', use: { viewport: { width: 1280, height: 900 } } },
  { tag: 'phone', use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 } },
]) {
  test.describe(vp.tag, () => {
    test.use(vp.use)

    test('source: edit details and analyze media (P10, P11, P05)', async ({ page }) => {
      await common(page)
      await drama(page, 2, {
        media_type: 'audio_drama', genre: 'xianxia', publication_status: 'ongoing', chapter_count: 244, episode_number: 3,
        source_url: 'https://example.com/tgcf', episode_summary: 'Xie Lian meets San Lang on the ox cart; the ghost bride case is still open.',
      })
      await media(page, 2, true)
      await page.goto('/#/drama/2/source')
      await openSection(page, 'Edit details')
      await page.getByLabel('Series', { exact: true }).selectOption({ label: '+ New series…' }, { timeout: 2000 }).catch(() => undefined)
      await shot(page, `source-details-${vp.tag}`, 'section[aria-label="Edit details"]')
      await openSection(page, 'Fill in details')
      await page.locator('.segmented label', { hasText: /^From the audio$/ }).click()
      await page.getByRole('button', { name: 'Analyze media' }).click()
      await page.getByTestId('analysis').waitFor()
      await shot(page, `source-analyze-${vp.tag}`, 'section[aria-label="Fill in details"]')
    })

    test('transcribe: speakers with corrections and estimates (D03, D04, D06)', async ({ page }) => {
      await common(page)
      await media(page, 1, false)
      await page.route('**/api/diarization/dramas/1/config', (r) =>
        r.fulfill({
          json: {
            drama_id: 1, hf_token_configured: true, expected_speakers: 3, min_speakers: null, max_speakers: null,
            last_device: 'cpu', audio_available: true, manual_speaker_count: 4,
          },
        }))
      await page.goto('/#/drama/1/source')
      await openTranscribeOptions(page)
      await openSection(page, 'Speakers')
      await shot(page, `transcribe-speakers-${vp.tag}`, 'section[aria-label="Transcribe"]')
    })

    test('translate: glossary without a series and Ollama unreachable (X09, X24)', async ({ page }) => {
      await common(page)
      await drama(page, 2, { series_id: null })
      await page.route('**/api/translate-run/dramas/2/config', async (r) => {
        const real = await (await r.fetch()).json()
        const engines = real.engines.some((e: { name: string }) => e.name === 'ollama')
          ? real.engines
          : [...real.engines, { name: 'ollama', label: 'Ollama', key_configured: true, models: [] }]
        await r.fulfill({ json: { ...real, engines, translation_engine: 'ollama', ollama_reachable: false, line_count: 40, untranslated_count: 40 } })
      })
      await page.goto('/#/drama/2/translate')
      await page.getByRole('region', { name: 'Translate run' }).waitFor().catch(() => undefined)
      await shot(page, `translate-run-${vp.tag}`, 'section[aria-label="Translate run"]')
      await openSection(page, 'Glossary')
      await shot(page, `translate-glossary-${vp.tag}`, 'details.section:has(> summary .section-title:text-is("Glossary"))')
    })

    test('translate: series cast bulk pronouns (X16)', async ({ page }) => {
      await common(page)
      await drama(page, 1, { series_id: 7 })
      await page.route('**/api/characters/series/7/characters', (r) =>
        r.fulfill({
          json: [
            { id: 13, character_name: 'Jiang Cheng', aliases: '', notes: '', pronouns: '' },
            { id: 12, character_name: 'Lan Zhan', aliases: 'Lan Wangji', notes: 'Speaks formally.', pronouns: 'he/him' },
            { id: 11, character_name: 'Wei Ying', aliases: 'Wei Wuxian', notes: '', pronouns: 'he/him' },
          ],
        }))
      await page.goto('/#/drama/1/translate')
      await openSection(page, 'Characters')
      await openSection(page, 'Series cast')
      await openSection(page, 'Bulk pronouns', 2000).catch(() => undefined)
      await page.getByLabel('Select Jiang Cheng').check({ timeout: 2000 }).catch(() => undefined)
      await page.getByLabel('Select Lan Zhan').check({ timeout: 2000 }).catch(() => undefined)
      await shot(page, `series-cast-${vp.tag}`, 'details.section:has(> summary .section-title:text-is("Series cast"))')
    })

    test('dub: narration with untranslated lines (U01)', async ({ page }) => {
      await common(page)
      await page.route('**/api/dub/dramas/1/config', (r) =>
        r.fulfill({
          json: {
            drama_id: 1, content_mode: 'novel_narration', is_narration: true, narration_language: 'translation',
            narration_language_options: ['translation', 'original'], source_language: 'zh',
            tts_engines: [{ key: 'edge_tts', label: 'Edge TTS (free, online, more natural)', requires_internet: true }],
            defaults: null, speakers: [], gpu_required: false, speakable_line_count: 28, track_available: false,
            gpt_sovits_configured: false, can_keep_background: false,
          },
        }))
      await page.route('**/api/dub/dramas/1/pacing', (r) => r.fulfill({ json: { available: false, counts: {}, lines: [] } }))
      await page.route('**/api/workflow/dramas/1/progress', (r) =>
        r.fulfill({
          json: {
            drama_id: 1, stage_index: 3, stage: 'dub', line_count: 40, untranslated_count: 12, flagged_count: 0,
            has_audio: false, has_dub_track: false, exported: false,
            stages: ['source', 'translate', 'review', 'dub', 'export'].map((key) => ({ key, state: key === 'dub' ? 'current' : 'done' })),
          },
        }))
      await page.goto('/#/drama/1/dub')
      await page.getByRole('region', { name: 'Dub' }).waitFor().catch(() => undefined)
      await shot(page, `dub-${vp.tag}`, 'section[aria-label="Dub"]')
    })
  })
}
