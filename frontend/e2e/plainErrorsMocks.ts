import type { Page } from '@playwright/test'

// Drama 1's Export stage with what a fresh install has: no narration, no dub. Only GETs reach the real seeded API; the rest is mocked.

export const progress = (over: object = {}) => ({
  drama_id: 1, stage_index: 4, stage: 'review', line_count: 5, untranslated_count: 0,
  flagged_count: 0, has_audio: true, has_dub_track: false, has_narration_track: false, exported: false,
  stages: ['source', 'translate', 'review', 'export'].map((key) => ({ key, state: 'pending' })),
  ...over,
})

export const mockProgress = (page: Page, over: object = {}) =>
  page.route('**/api/workflow/dramas/1/progress', (r) => r.fulfill({ json: progress(over) }))
