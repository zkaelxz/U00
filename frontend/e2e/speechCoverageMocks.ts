import type { Page } from '@playwright/test'

export const GAPS = [
  { start: 65, end: 71.5, seconds: 6.5, speech_seconds: 6.0, raw_status: 'lost_after', raw_text: '好的好的', after_line_id: 4, before_line_id: 5 },
  { start: 300, end: 304, seconds: 4, speech_seconds: 3.5, raw_status: 'none', raw_text: '', after_line_id: 9, before_line_id: 10 },
]

const report = {
  audio_seconds: 600, speech_seconds: 200, covered_seconds: 150, covered_percent: 75, vad_threshold: 0.3,
  min_gap_seconds: 2, raw_available: true, gaps_total: 2, gaps: GAPS, failed_reason: null, detail: null,
}

// The drama has audio; the check starts on POST and is "done" on the next read.
export async function mockSpeechCoverage(page: Page): Promise<{ posts: string[] }> {
  const seen = { posts: [] as string[] }
  let started = false
  await page.route('**/api/media/dramas/1/status', (route) =>
    route.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }),
  )
  await page.route('**/api/transcribe/dramas/1/speech-coverage', (route) => {
    if (route.request().method() === 'POST') {
      seen.posts.push(route.request().postData() ?? '')
      started = true
      return route.fulfill({ json: { job_id: 'speechcov_1' } })
    }
    return route.fulfill({
      json: started
        ? { job_id: 'speechcov_1', status: 'done', progress: 1, message: '', result: report }
        : { job_id: '', status: 'idle', progress: 0, message: '', result: null },
    })
  })
  return seen
}
