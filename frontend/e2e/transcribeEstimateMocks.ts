import type { Page } from '@playwright/test'

// Drama 1's Transcribe card in Whisper mode with audio, with the config,
// media, length and run mocked. `state` is read on every job poll, so a test
// changes the job by assigning to it.
export interface MockJob {
  progress: number | null
  message: string
}

export async function mockTranscribeCard(
  page: Page,
  opts: { durationSeconds: number; cached?: boolean; separate?: boolean; measuredSpeed?: number },
  state: MockJob,
) {
  await page.route('**/api/transcribe/dramas/1/config', async (route) => {
    const resp = await route.fetch()
    await route.fulfill({
      response: resp,
      json: {
        ...(await resp.json()),
        transcript_mode: 'whisper',
        asr_backend_choice: 'whisper',
        whisper_size: 'large-v3',
        whisper_model_cached: opts.cached ?? true,
        measured_speed: opts.measuredSpeed ?? null,
        separate_vocals_first: opts.separate ?? false,
        use_groq: false,
        audio_available: true,
      },
    })
  })
  await page.route('**/api/media/dramas/1/status', (r) =>
    r.fulfill({ json: { drama_id: 1, has_audio: true, has_source_video: false, upload_max_mb: 500 } }),
  )
  await page.route('**/api/metadata/dramas/1/analyze-media', (r) =>
    r.fulfill({
      json: {
        drama_id: 1, duration_seconds: opts.durationSeconds, has_video: false, has_audio: true,
        audio_track_count: 1, sample_rate: 44100,
      },
    }),
  )
  await page.route('**/api/settings', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const resp = await route.fetch()
    await route.fulfill({ response: resp, json: { ...(await resp.json()), use_gpu: false } })
  })
  // No such job until Transcribe is pressed (the real API answers 404).
  let started = false
  await page.route('**/api/transcribe/dramas/1/run', (r) => {
    started = true
    return r.fulfill({ json: { job_id: 'transcribe_1' } })
  })
  await page.route('**/api/jobs/transcribe_1', (r) =>
    !started
      ? r.fulfill({ status: 404, json: { error: { code: 'not_found', message: 'No job.' } } })
      : r.fulfill({
          json: {
            job_id: 'transcribe_1', status: 'running', progress: state.progress, message: state.message,
            error: null, description: null, gpu_touching: true, started_at: null, finished_at: null,
            updated_at: Date.now() / 1000,
          },
        }),
  )
  await page.route('**/api/jobs/transcribe_1/cancel', (r) => r.abort())
}
