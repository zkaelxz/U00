import type { Page } from '@playwright/test'

// Drama 1's Export and Dub stages with what a fresh install has: no narration,
// no dub, no edge-tts. Only GETs reach the real seeded API; the rest is mocked.

export const progress = (over: object = {}) => ({
  drama_id: 1, stage_index: 4, stage: 'review', line_count: 5, untranslated_count: 0,
  flagged_count: 0, has_audio: true, has_dub_track: false, has_narration_track: false, exported: false,
  stages: ['source', 'translate', 'review', 'dub', 'export'].map((key) => ({ key, state: 'pending' })),
  ...over,
})

export const mockProgress = (page: Page, over: object = {}) =>
  page.route('**/api/workflow/dramas/1/progress', (r) => r.fulfill({ json: progress(over) }))

export const dubConfig = (engineReason: string | null) => ({
  drama_id: 1, content_mode: 'audio_drama', is_narration: false, narration_language: 'translation',
  narration_language_options: ['translation', 'original'], source_language: 'zh',
  tts_engines: [{ key: 'edge_tts', label: 'Edge TTS', requires_internet: true, unavailable_reason: engineReason }],
  defaults: { max_speedup: 1.3, max_slowdown: 0.85, speedup_range: [1, 2], slowdown_range: [0.5, 1] },
  speakers: [], gpu_required: false, speakable_line_count: 5, track_available: false,
  gpt_sovits_configured: false, can_keep_background: false,
})

export async function mockDub(page: Page, engineReason: string | null) {
  await page.route('**/api/dub/dramas/1/config', (r) => r.fulfill({ json: dubConfig(engineReason) }))
  await page.route('**/api/dub/dramas/1/pacing', (r) => r.fulfill({ json: { available: false, counts: {}, lines: [] } }))
  await mockProgress(page)
}
