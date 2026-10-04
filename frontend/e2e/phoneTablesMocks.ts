import type { Page } from '@playwright/test'

import { ME } from './authMocks'

// Realistic data for the Characters, Glossary and Bulk batches tables: long names, a custom-pronoun speaker, aliases
// and banned words, one failed batch. Drama 1 is reported as being in series 7 so the glossary shows.

const speaker = (label: string, over: object = {}) => ({
  speaker_label: label, character_name: '', voice_actor: '', pronouns: '', tts_voice: '',
  offline_voice: '', clone_engine: '', voice_design: '', has_ref_audio: false, ref_text_present: false,
  series_character_id: null, series_character_name: '', line_count: 3, series_pronouns: '',
  sample_lines: [], ...over,
})

const term = (id: number, over: object = {}) => ({
  id, term_original: '林晚', term_translation: 'Lin Wan', aliases: [], banned_translations: [], enforce_exact: false, ...over,
})

const batch = (id: number, over: object = {}) => ({
  bulk_job_id: id, engine: 'gemini', model: 'gemini-2.5-pro-with-a-long-model-name', kind: 'reflect', stage: 'critique',
  pipeline_id: 'p1', status: 'submitted', pending: true, cancellable: true, line_count: 1200, scheduled_for: null,
  result_summary: null, last_error: null, submitted_at: '2026-09-29T09:00:00', updated_at: '2026-09-29T09:05:00', ...over,
})

export async function mockPhoneTables(page: Page) {
  await page.route(/\/api\/auth\/me$/, (route) => route.fulfill({ json: ME.authOff }))
  await page.route('**/api/library/dramas/1', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), series_id: 7 } })
  })
  await page.route('**/api/characters/series/7/characters', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/characters/dramas/1', (route) => route.fulfill({
    json: [
      speaker('SPEAKER_00', { character_name: 'Lin Wan', pronouns: 'she/her', tts_voice: 'zh-CN-XiaoxiaoNeural', line_count: 128, has_ref_audio: true, ref_text_present: true }),
      speaker('SPEAKER_01', { pronouns: 'xe/xem', line_count: 40 }),
    ],
  }))
  await page.route('**/api/characters/dramas/1/clone-engines', (route) =>
    route.fulfill({ json: { source_language: 'zh', default_engine: 'f5tts', engines: [] } }))
  await page.route('**/api/characters/voice-bank', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/glossary/dramas/1/terms', (route) => route.fulfill({
    json: [
      term(1, { aliases: ['晚晚', '小晚'], banned_translations: ['Wanwan'], enforce_exact: true }),
      term(2, { term_original: '玄天宗', term_translation: 'Xuantian Sect (the Mysterious Heaven Sect)' }),
    ],
  }))
  await page.route('**/api/translate-run/dramas/1/bulk', (route) => route.fulfill({
    json: { drama_id: 1, jobs: [batch(21), batch(20, { status: 'auth_error', last_error: 'The provider rejected the key.' })] },
  }))
}
