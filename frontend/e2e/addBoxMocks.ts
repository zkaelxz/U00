import type { Page } from '@playwright/test'

import type { MediaLinkProbe } from '../src/api/linkDetails'
import { json } from './mockHelpers'

// The Add box's link side: the free link probe and One-Click Translate (settings, start, runs), all mocked.

export const VIDEO_LINK = 'https://www.youtube.com/watch?v=abc123'

export const probeOf = (o: Partial<MediaLinkProbe>): MediaLinkProbe => ({
  kind: 'video', title: '', channel: '', release_date: '', is_live: false, summary: '', platform: '',
  series_suggestion: '', note: '', display_url: '', ...o,
})

export interface QuickVideoMock { started: unknown[] }

export async function mockAddLink(page: Page, probe: MediaLinkProbe): Promise<QuickVideoMock> {
  const m: QuickVideoMock = { started: [] }
  await page.route('**/api/media-link/probe', (r) => json(r, probe))
  await page.route('**/api/quick-video/settings', (r) => json(r, {
    profile_id: 3,
    profiles: [{ id: 3, name: 'Fast', ready: true }],
    summary: [
      { label: 'Engine and model', value: 'deepseek-chat', engine: 'deepseek' },
      { label: 'Batch size', value: '30', engine: null },
    ],
  }))
  await page.route('**/api/quick-video/start', (r) => {
    m.started.push(r.request().postDataJSON())
    return json(r, { drama_id: 77, watch_once: true })
  })
  await page.route('**/api/quick-video/runs', (r) => json(r, {
    items: m.started.length
      ? [{ drama_id: 77, title: 'Episode 1', watch_once: true, state: 'running', step: 'download', reason: null }]
      : [],
  }))
  return m
}
