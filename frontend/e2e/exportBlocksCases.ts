import { expect, type Page } from '@playwright/test'

// Shared by the desktop and phone specs: the same folding rules must hold on both layouts.
export const BLOCKS = ['Audiobook', 'Burned-in video', 'Video with a subtitle track', 'Video with the dub audio'] as const

export const toggle = (page: Page, title: string) => page.getByRole('button', { name: title, exact: true })

export async function expectExpanded(page: Page, expected: Record<string, boolean>): Promise<void> {
  for (const title of BLOCKS) await expect(toggle(page, title)).toHaveAttribute('aria-expanded', String(expected[title]))
}

export const DEFAULT_OPEN = {
  Audiobook: false,
  'Burned-in video': false,
  'Video with a subtitle track': true,
  'Video with the dub audio': false,
}

export const softsubJob = (status: string, extra: object = {}) => ({
  job_id: 'fake-export', status, progress: 0.5, message: 'encoding', error: null, description: null,
  gpu_touching: false, started_at: 1, finished_at: null, updated_at: 1, ...extra,
})

// A softsub export that keeps running until `finish()`, then offers its file.
export async function mockSoftsubRun(page: Page) {
  let finished = false
  await page.route('**/api/export/dramas/1/softsub-video', (route) => route.fulfill({ json: { job_id: 'fake-export' } }))
  await page.route('**/api/jobs/fake-export', (route) =>
    route.fulfill({ json: finished ? softsubJob('done', { finished_at: 2 }) : softsubJob('running') }),
  )
  await page.route('**/api/artifacts/dramas/1/softsub_video/info', (route) =>
    finished
      ? route.fulfill({ json: { name: 'softsub_video_1.mkv', size: 1024, kind: 'softsub_video' } })
      : route.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'none' } } }),
  )
  return { finish: () => (finished = true) }
}
