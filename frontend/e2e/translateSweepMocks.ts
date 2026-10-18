import { type Page } from '@playwright/test'

import { estimate as audioEstimate, finished as audioFinished } from './sweepMocks'

// Mocked API answers for the translation sweep spec: a sweep spends on a real engine, which e2e never has.
export const sets = [
  { stage: 'translation', tier: 'application', set_name: 'My scenes', case_count: 60, with_reference: 60, excluded: 0 },
]
export const estimate = {
  set_name: 'My scenes', language: 'zh', engine: 'claude', model: null, series_id: null, tuning_cases: 42, holdout_cases: 18,
  tuning_titles: 4, holdout_titles: 2, small_set: false, min_set_cases: 50,
  models: [
    { engine: 'claude', model: 'haiku', label: 'haiku', included: true, reason: null },
    { engine: 'claude', model: 'opus', label: 'opus', included: false, reason: 'opus costs more than the remaining monthly cap allows.' },
  ],
  knobs: [
    { key: 'batch_size', label: 'Batch size', values: [15, 30, 60], included: true, reason: null },
    { key: 'context_window', label: 'Context lines before', values: [3, 6, 10, 20], included: true, reason: null },
    { key: 'reflect', label: 'Reflect', values: [false, true], included: false, reason: "This engine can't take Reflect instructions." },
  ],
  not_swept: ['Judge scores: only chrF is compared.'],
  passes_done: 2, passes_to_run: 8, estimated_cost_usd: 1.8, cap_applies: true, monthly_cap_usd: 20, month_spend_usd: 4,
  remaining_usd: 16, monthly_refusal: null, estimate_above_cap: false,
}
export const finished: Record<string, unknown> = {
  ...audioFinished, id: 'tsw', set_name: 'My scenes', engine: 'claude', model: null, series_id: null, estimate: null, decision: null,
  applies: [{ key: 'translate_batch_size', label: 'Batch size', value: '15' }],
  recommendation: {
    ...(audioFinished.recommendation as Record<string, unknown>),
    text: 'The winner also beats the default on the held-out titles by the margin. Approve to save it as a setting profile.',
    margin_rule: 'A change is recommended only when it beats the default on the held-out titles by at least 1.5 chrF points and at least twice the noise (0.4 points).',
    default: { engine: 'claude', model: null, changes: {}, label: 'claude' },
    winner: { engine: 'claude', model: null, changes: { batch_size: 15 }, label: 'claude, Batch size 15' },
    tuning: { default_score: 0.48, winner_score: 0.52 },
    holdout: { default_score: 0.47, winner_score: 0.5, delta: 0.03, default_run_id: 41, winner_run_id: 40, default_errors: 0, winner_errors: 0 },
    tags: [],
    steps: [
      { step: 'noise', value: null, half: 'tuning', setup: 'claude', run_id: 30, score: 0.48, errors: 0, kept: null },
      { step: 'batch_size', value: 15, half: 'tuning', setup: 'claude, Batch size 15', run_id: 32, score: 0.52, errors: 0, kept: true },
    ],
  },
}

export async function mockTranslateSweep(page: Page, current: () => unknown) {
  await page.route('**/api/benchmark/sets', (r) => r.fulfill({ json: { sets } }))
  await page.route('**/api/benchmark/sweep', (r) => r.fulfill({ json: { sweep: null } }))
  await page.route('**/api/benchmark/sweep/decisions', (r) => r.fulfill({ json: { decisions: [] } }))
  await page.route('**/api/benchmark/sweep/estimate', (r) => r.fulfill({ json: audioEstimate }))
  await page.route('**/api/benchmark/translate-sweep', (r) => r.request().method() === 'GET'
    ? r.fulfill({ json: { sweep: current() } })
    : r.fulfill({ json: { job_id: 'benchmark_lab', sweep: { ...finished, status: 'queued', recommendation: null } } }))
  await page.route('**/api/benchmark/translate-sweep/decisions', (r) => r.fulfill({ json: { decisions: [] } }))
  await page.route('**/api/benchmark/translate-sweep/estimate', (r) => r.fulfill({ json: estimate }))
}
