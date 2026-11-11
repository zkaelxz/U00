/** Hand-run translation results copied from the 2026-11-11 write-ups; not data the app recorded. */

export interface HandRunRow {
  model: string
  /** Seconds for the 90-line test chunk. */
  seconds: number
  /** Cost of the chunk in USD; 0 for a local model. */
  costUsd: number
  /** True when the cost is price x tokens worked out by hand, not logged by the app. */
  costEstimated?: boolean
  /** What reading the output showed; there is no numeric quality score. */
  reading: string
}

export const HAND_RUN_TEST = {
  title: 'Mandarin stream chunk, 90 lines (2026-11-11)',
  source: 'baihe/translation-bench-v100/stream-test/stream-test-2026-11-11.md and thinking-on-off-2026-11-11.md in the Library',
  note: 'One run each, judged by reading, no native check. Differences of a line or two are within run-to-run noise.',
}

export const HAND_RUN_ROWS: HandRunRow[] = [
  { model: 'Sonnet 5.5 (thinking on, app default)', seconds: 27.5, costUsd: 0.0501, reading: 'Best on idioms and the nickname.' },
  { model: 'Sonnet 5.5, thinking off', seconds: 12.5, costUsd: 0.03, reading: 'Slightly worse on slang and the nickname.' },
  { model: 'GPT-6 Luna (default)', seconds: 66.7, costUsd: 0.0038, reading: 'About as good as low; read the drunk slang right once.' },
  { model: 'GPT-6 Luna, low reasoning', seconds: 23.9, costUsd: 0.0015, reading: 'About the same as default; lost that slang line.' },
  { model: 'GPT-6 Luna, low reasoning + glossary fixes', seconds: 25.1, costUsd: 0.00153, reading: 'Fixed the nickname and the drunk slang; the glossary helped more than extra reasoning.' },
  { model: 'GPT-6 Luna, none reasoning', seconds: 14.6, costUsd: 0.001, reading: 'About the same size of mistakes; one line wrong.' },
  { model: 'GPT-6.1 Terra, low reasoning', seconds: 22.7, costUsd: 0.012, reading: 'Small step up from Luna low on idioms, about 8x its cost.' },
  { model: 'DeepSeek flash (thinking off, app default)', seconds: 8.9, costUsd: 0.0028, reading: 'Literal; got the idiom and slang wrong.' },
  { model: 'DeepSeek flash, thinking on', seconds: 44.0, costUsd: 0.0105, reading: 'More consistent nickname; about 3.7x the cost for a small gain.' },
  { model: 'Gemini 3.8 Flash (default)', seconds: 70.7, costUsd: 0.0415, reading: 'Got the idiom right; added a gender to the nickname.' },
  { model: 'Gemini 3.8 Flash, low', seconds: 22.9, costUsd: 0.0152, reading: 'About 3x cheaper, but lost the idiom and invented an insult.' },
  { model: 'Gemini 3.5 Flash-Lite (default)', seconds: 6.1, costUsd: 0.0064, reading: 'Fine for the price; name drift.' },
  { model: 'gemma4:31b-cloud', seconds: 13.1, costUsd: 0.0017, costEstimated: true, reading: 'Natural; a notch below Luna on slang.' },
  { model: 'translategemma:27b (local, V100)', seconds: 67.6, costUsd: 0, reading: 'Usable gist for free; clearly behind the cloud models on slang and context.' },
]

/** Competition ranks (1 = fastest); equal times share a rank. */
export function speedRanks(rows: HandRunRow[]): number[] {
  return rows.map((r) => 1 + rows.filter((o) => o.seconds < r.seconds).length)
}
