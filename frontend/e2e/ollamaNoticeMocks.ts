import type { Page } from '@playwright/test'

export const OLLAMA_NOTICE =
  "Ollama still has a model loaded (gemma4:26b), which may make transcription run out of GPU memory and fall back to the CPU. Free it with: ollama stop gemma4:26b, or turn on Settings > Free Ollama's GPU memory before transcribing."

// A finished transcription whose result carries the Ollama notice, as the
// Jobs list receives it (the notice is also the job's outcome message).
export async function mockFinishedTranscribeWithNotice(page: Page) {
  await page.route('**/api/jobs', (r) =>
    r.fulfill({
      json: {
        count: 1,
        items: [
          {
            job_id: 'transcribe_1', status: 'done', progress: 1, message: 'Done', error: null,
            description: 'Transcribe Signal', gpu_touching: true, started_at: 10, finished_at: 20, updated_at: 20,
            result: { line_count: 12, ollama_notice: OLLAMA_NOTICE },
            outcome: 'partial', outcome_message: OLLAMA_NOTICE,
          },
        ],
      },
    }),
  )
}
