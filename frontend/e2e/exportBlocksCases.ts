import { expect, type Page } from '@playwright/test'

import { exportSection } from './exportBlocks'

// Shared by the desktop and phone specs: the same folding rules must hold on both layouts.
export const BLOCKS = [
  'Video with a subtitle track',
  'Burned-in video',
  'Video with the dub audio',
  'Audiobook',
  'Subtitle file',
  'EPUB',
] as const

export const summaryOf = (page: Page, title: string) => exportSection(page, title).locator('summary').first()

export async function expectOpen(page: Page, expected: Partial<Record<(typeof BLOCKS)[number], boolean>>): Promise<void> {
  for (const [title, open] of Object.entries(expected)) await expect(exportSection(page, title)).toHaveJSProperty('open', open)
}

// Which block leads and starts open, per seeded title kind (drama 1 audio, 2 novel, 3 video).
export const FIRST_OPEN = {
  video: 'Video with a subtitle track',
  audio: 'Audiobook',
  novel: 'EPUB',
  subtitle: 'Subtitle file',
} as const

export const softsubJob = (status: string, extra: object = {}) => ({
  job_id: 'fake-export', status, progress: 0.5, message: 'encoding', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, ...extra,
})

// A softsub export that keeps running until `finish()`, then offers its file.
export async function mockSoftsubRun(page: Page, id = 1) {
  let finished = false
  await page.route(`**/api/export/dramas/${id}/softsub-video`, (route) => route.fulfill({ json: { job_id: 'fake-export' } }))
  await page.route('**/api/jobs/fake-export', (route) =>
    route.fulfill({ json: finished ? softsubJob('done', { finished_at: 2 }) : softsubJob('running') }),
  )
  await page.route(`**/api/artifacts/dramas/${id}/softsub_video/info`, (route) =>
    finished
      ? route.fulfill({ json: { name: 'softsub_video_1.mkv', size: 1024, kind: 'softsub_video' } })
      : route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'none' } } }),
  )
  return { finish: () => (finished = true) }
}
