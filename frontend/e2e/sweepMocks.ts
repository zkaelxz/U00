import { type Page } from '@playwright/test'

// Mocked API answers for the Settings sweep specs (desktop and phone): the seeded library has no
// audio clips and no model, so a sweep can't really run there.
const sets = [
  { stage: 'transcription', tier: 'application', set_name: 'My drama clips', case_count: 64, with_reference: 64, excluded: 0 },
  { stage: 'translation', tier: 'public', set_name: 'flores', case_count: 3, with_reference: 3, excluded: 0 },
]
export const estimate = {
  set_name: 'My drama clips', language: 'zh', use_gpu: true, tuning_cases: 45, holdout_cases: 19, tuning_titles: 4,
  holdout_titles: 2, audio_seconds: 1260, durations_guessed: false, small_set: false, min_set_cases: 50,
  models: [
    { engine: 'whisper', model: 'large-v3', label: 'large-v3', included: true, reason: null },
    { engine: 'whisper', model: 'large-v3-turbo', label: 'large-v3-turbo', included: false,
      reason: "large-v3-turbo isn't downloaded on this PC. Run a title with it once; the sweep never downloads a model." },
  ],
  knobs: [
    { key: 'beam_size', label: 'Beam size', values: [1, 3, 5], included: true, reason: null },
    { key: 'min_silence_ms', label: 'Minimum silence (ms)', values: [200, 300, 500, 800], included: true, reason: null },
    { key: 'separate_vocals_first', label: 'Separate vocals first', values: [false, true], included: false,
      reason: "The vocal separation model isn't downloaded yet. Run Separate vocals first once on a title; the sweep does not download it." },
  ],
  not_swept: ['Initial prompt: a title builds it from its glossary, and the Lab has no glossary to build it from.'],
  passes_done: 3, passes_to_run: 9, estimated_seconds: 2700, estimated_cost_usd: 0, monthly_cap_usd: 20, monthly_refusal: null,
}
const step = (s: string, value: unknown, half: string, setup: string, score: number, kept: boolean | null, run_id: number) =>
  ({ step: s, value, half, setup, run_id, score, errors: 0, kept })
export const finished: Record<string, unknown> = {
  id: 'abc', set_name: 'My drama clips', language: 'zh', use_gpu: true, status: 'done',
  started_at: '2026-10-18T01:00:00', finished_at: '2026-10-18T02:00:00', note: null, passes_run: 9, passes_reused: 3,
  estimate: null, decision: null,
  applies: [
    { key: 'translation_engine', label: 'Translation engine', value: 'claude' },
    { key: 'asr_backend_choice', label: 'Speech recognizer', value: 'whisper' },
    { key: 'whisper_size', label: 'Whisper model', value: 'large-v3' },
    { key: 'min_silence_ms', label: 'Minimum silence (ms)', value: '500' },
    { key: 'include_genre_notes', label: 'Genre notes in the prompt', value: 'on' },
    { key: 'default_female_pronouns', label: 'Default to she/her', value: 'off' },
    { key: 'translate_start', label: 'Translate form starting style and model', value: "back to the engine's defaults" },
  ],
  recommendation: {
    state: 'adopt', noise: 0.004, margin: 0.02,
    text: 'The winner also beats the default on the held-out titles by the margin. Approve to save it as a setting profile.',
    margin_rule: 'A change is recommended only when it beats the default on the held-out titles by at least 2 CER points and at least twice the noise (0.4 points), so here by 2.0 points, with no more failed clips.',
    default: { engine: 'whisper', model: 'large-v3', changes: {}, label: 'large-v3' },
    winner: { engine: 'whisper', model: 'large-v3', changes: { min_silence_ms: 500 }, label: 'large-v3, Minimum silence (ms) 500' },
    tuning: { default_score: 0.871, winner_score: 0.902 },
    holdout: { default_score: 0.858, winner_score: 0.889, delta: 0.031, default_run_id: 41, winner_run_id: 40, default_errors: 0, winner_errors: 0 },
    tags: [
      { tag: 'music', cases: 11, default_score: 0.81, winner_score: 0.85, delta: 0.04, too_small: true },
      { tag: 'quiet', cases: 16, default_score: 0.9, winner_score: 0.92, delta: 0.02, too_small: false },
    ],
    steps: [
      step('noise', null, 'tuning', 'large-v3', 0.869, null, 30),
      step('noise', null, 'tuning', 'large-v3', 0.873, null, 31),
      step('beam_size', 1, 'tuning', 'large-v3, Beam size 1', 0.85, false, 32),
      step('min_silence_ms', 500, 'tuning', 'large-v3, Minimum silence (ms) 500', 0.902, true, 34),
      step('holdout', 'winner', 'holdout', 'large-v3, Minimum silence (ms) 500', 0.889, null, 40),
      step('holdout', 'default', 'holdout', 'large-v3', 0.858, null, 41),
    ],
    case_counts: { tuning: 45, holdout: 19 }, small_set: false,
  },
}

// current() is read on every GET, so a mocked decision is what the page reloads.
export async function mockLab(page: Page, current: () => unknown) {
  await page.route('**/api/benchmark/sets', (r) => r.fulfill({ json: { sets } }))
  await page.route('**/api/benchmark/sweep', (r) => r.request().method() === 'GET'
    ? r.fulfill({ json: { sweep: current() } })
    : r.fulfill({ json: { job_id: 'benchmark_lab', sweep: { ...finished, status: 'queued', recommendation: null, passes_run: 0, passes_reused: 0 } } }))
  await page.route('**/api/benchmark/sweep/decisions', (r) => r.fulfill({ json: { decisions: [] } }))
  await page.route('**/api/benchmark/sweep/estimate', (r) => r.fulfill({ json: estimate }))
}
