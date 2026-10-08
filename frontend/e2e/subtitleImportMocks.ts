import type { Page } from '@playwright/test'

// Mocks for the Source stage's "Import subtitle file" on seeded drama 2: the
// parser and the import are covered by pytest, so here the two routes are
// faked and every request is recorded. The upload is multipart, so options are
// read out of the raw body text.

export const PREVIEW = {
  format: 'srt', encoding: 'gb18030', encoding_guessed: true, cue_count: 42, duration_seconds: 311.5,
  detected_language: 'zh', bilingual_suspected: false, blocking: false, mode: 'source',
  problems: [
    { code: 'encoding_guess', severity: 'warning', message: 'The text isn’t UTF-8; it was read as gb18030. Check the sample, and pick another encoding if the characters look wrong.', count: 1 },
    { code: 'overlap', severity: 'warning', message: 'A cue starts before the previous one ends (3 cues, first at cue 5).', count: 3 },
  ],
  sample: [{ start: 1, end: 2, text: '你好' }, { start: 3, end: 4, text: '再见\nBye' }],
  existing_line_count: 5, replaces_lines: 5, matched_lines: 0, unmatched_cues: 0, overwrites: 0, unsplit_cues: 0,
  blocked_reason: null,
}

export interface SubtitleSeen {
  previews: string[]
  applies: string[]
  retimes: unknown[]
}

const field = (body: string, name: string) => new RegExp(`name="${name}"\\r?\\n\\r?\\n([^\\r\\n]*)`).exec(body)?.[1] ?? null

export async function mockSubtitleImport(page: Page, preview: Record<string, unknown> = {}): Promise<SubtitleSeen> {
  const seen: SubtitleSeen = { previews: [], applies: [], retimes: [] }
  const base = '**/api/subtitle-import/dramas/2'
  await page.route(`${base}/preview`, (r) => {
    const body = r.request().postData() ?? ''
    seen.previews.push(body)
    const mode = field(body, 'mode') ?? 'source'
    const translation = mode === 'translation'
    return r.fulfill({ json: { ...PREVIEW, mode, replaces_lines: translation ? 0 : 5,
      matched_lines: translation ? 4 : 0, unmatched_cues: translation ? 2 : 0, overwrites: translation ? 1 : 0, ...preview } })
  })
  await page.route(`${base}/apply`, (r) => {
    const body = r.request().postData() ?? ''
    seen.applies.push(body)
    const needs = field(body, 'mode') === 'translation' ? 'confirm_overwrite' : 'confirm_replace_lines'
    if (field(body, needs) !== 'true') {
      return r.fulfill({ status: 422, json: { error: { code: 'validation_error', message: 'Confirm to continue.', details: { reason: needs } } } })
    }
    return r.fulfill({ json: { mode: field(body, 'mode') ?? 'source', format: 'srt', encoding: 'gb18030', lines_written: 42,
      line_ids: [101, 102, 103], replaced_lines: 5, matched_lines: 0, unmatched_cues: 0, undo: { history_id: 9, lines_fingerprint: 'x' } } })
  })
  await page.route('**/api/transcribe/dramas/2/retime/run', (r) => {
    seen.retimes.push(r.request().postDataJSON())
    return r.fulfill({ json: { job_id: 'retime_2', drama_id: 2, line_count: 3 } })
  })
  await page.route('**/api/jobs/retime_2', (r) =>
    r.fulfill({ json: { job_id: 'retime_2', status: 'running', progress: 0.1, message: '', error: null, description: null,
      gpu_touching: true, started_at: 1, finished_at: null, updated_at: 1 } }))
  return seen
}

export const SRT_FILE = { name: 'episode.srt', mimeType: 'application/x-subrip', buffer: Buffer.from('1\n00:00:01,000 --> 00:00:02,000\nhi\n') }
