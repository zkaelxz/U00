/** Hand-run model comparisons copied from the 2026-11-11 Library write-ups; not data the app recorded. */

export interface HandRunRow {
  model: string
  /** Seconds used to rank speed; the midpoint when the write-up gives a range. Omitted when not measured. */
  seconds?: number
  /** Shown instead of `seconds` when the write-up gives a range or a rough figure. */
  timeLabel?: string
  /** Cost of the test's unit of work in USD; omitted when not measured. */
  costUsd?: number
  /** Shown instead of a dollar figure (for example a plan credit). */
  costLabel?: string
  /** True when the cost is price x tokens worked out by hand, not logged by the app. */
  costEstimated?: boolean
  /** What reading the output showed; there is no numeric quality score. */
  reading: string
}

export interface HandRunTest {
  id: string
  title: string
  /** What one time and one cost figure cover, for the column headings. */
  basis: string
  source: string
  note: string
  /** The stream test also shows a 2,000-line cost, scaled from the 90-line chunk as its write-up does. */
  scaleTo2000Lines?: boolean
  /** Rank cheapest first; used when speed was not measured. */
  rankCost?: boolean
  rows: HandRunRow[]
}

const STREAM_ROWS: HandRunRow[] = [
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

const TRICKY_ROWS: HandRunRow[] = [
  { model: 'Sonnet 5.5', seconds: 3.5, reading: 'Most natural and faithful to register; numbers 8/8. In plain mode it added a stray "(Teaching a fish to swim.)" to one line.' },
  { model: 'Gemini 3.8 Flash', seconds: 18.7, costUsd: 0.0138, reading: 'As good as Sonnet on this set; numbers 8/8. Thinks by default, so about 10x the flash-lite cost.' },
  { model: 'DeepSeek flash', seconds: 8.5, timeLabel: '7-10 s', reading: 'Close behind Sonnet; about 3x the output tokens; writes "Qingyun" without a glossary; numbers 8/8.' },
  { model: 'Gemini 3.1 Flash-Lite', seconds: 1.5, timeLabel: '~1.5 s', costUsd: 0.0008, reading: 'Best value; one literal idiom ("fishes three days, dries his nets two"); numbers 8/8.' },
  { model: 'Gemini 3.5 Flash-Lite', seconds: 1.5, timeLabel: '~1.5 s', costUsd: 0.0013, reading: 'Weakest of the three Gemini models: "Cut the crap" is too vulgar for the scene, one idiom off; numbers 8/8.' },
  { model: 'gemma4:31b-cloud', seconds: 1.1, reading: 'Fastest and mostly good; slips on two idioms; numbers 8/8.' },
  { model: 'GPT-6 Luna', seconds: 6.3, timeLabel: '5.1-7.5 s', reading: 'Keeps the fishing-and-nets idiom and the dead-back-to-life joke; numbers 8/8; writes "Qingyun" without a glossary.' },
  { model: 'GPT-6.1 Terra', seconds: 6.3, timeLabel: '5.9-6.6 s', reading: 'Flattens one idiom and drops a subject; numbers 8/8.' },
  { model: 'translategemma:27b (local, V100)', reading: 'Gets the three idiom lines (3, 4 and 12); numbers 8/8. Time for this request was not measured (about 1.04 s per cue in the Live-style timing).' },
  { model: 'translategemma:12b (local, V100)', seconds: 23, timeLabel: '12-34 s', reading: 'Best small local model on tone; missed one idiom; numbers 6/8 (25,000 became "Two thousand five hundred", 400 li became "400 miles").' },
  { model: 'translategemma:4b (local, V100)', seconds: 18, timeLabel: '17-19 s', reading: 'Most literal; the worst idiom errors. Numbers were not tested.' },
]

const COMIC_ROWS: HandRunRow[] = [
  { model: 'Haiku 5.5, crops only', costUsd: 0.0002, reading: 'Matched full-page dialogue, but one crop returned a picture description instead of a translation.' },
  { model: 'Haiku 5.5, full page (the app\'s picture model)', costUsd: 0.0006, reading: 'Dialogue as good as Sonnet 5.5 on these pages at about a fifteenth of the cost.' },
  { model: 'Gemini 3.1 Flash-Lite, page image', costUsd: 0.0006, reading: 'No dropped lines; on par with Haiku in a spot-check of fewer lines; same caption mistake as text-only Sonnet.' },
  { model: 'GPT-6 Luna, page image', costUsd: 0.0004, costEstimated: true, reading: 'Read well, but translated a watermark line. Price was unconfirmed at the time.' },
  { model: 'Gemini Flash-Lite (older), page image', costUsd: 0.0008, reading: 'Dropped one line.' },
  { model: 'Sonnet 5.5, text only', costUsd: 0.0023, reading: 'Dialogue about equal to the picture arms; worse on garbled OCR, sound effects, watermarks and tone.' },
  { model: 'Gemini Flash, page image', costUsd: 0.0043, reading: 'Not better than Haiku and 7 times the cost.' },
  { model: 'Sonnet 5.5, page image', costUsd: 0.0087, reading: 'Good, and better than text-only on sound effects, watermarks and tone.' },
  { model: 'Opus 5.5, page image', costLabel: 'plan credit', reading: 'Run on plan credit, so no API cost.' },
]

export const HAND_RUN_TESTS: HandRunTest[] = [
  {
    id: 'stream',
    title: 'Mandarin stream chunk, 90 lines (2026-11-11)',
    basis: '90 lines',
    source: 'baihe/translation-bench-v100/stream-test/stream-test-2026-11-11.md and thinking-on-off-2026-11-11.md',
    note: 'The app\'s own translate pipeline on a drunk-stream transcript. Gemini 3.1 Flash-Lite is left out: it returned nothing for the second batch of 30 lines in every run, so it is unrated here.',
    scaleTo2000Lines: true,
    rows: STREAM_ROWS,
  },
  {
    id: 'tricky',
    title: 'Tricky wuxia lines (16) and number lines (8) (2026-11-11)',
    basis: 'one 16-line request',
    source: 'baihe/translation-bench-v100/tricky-all-models-2026-11-11.md and gemini-tricky-2026-11-11.md (top level of the Library)',
    note: 'Costs are only given for the Gemini rows (tricky plus number lines). Ranges are ranked on their midpoint. Tester\'s overall order for the models it compared: Sonnet 5.5 >= Gemini 3.8 Flash > DeepSeek flash ~ Gemini 3.1 Flash-Lite > Gemini 3.5 Flash-Lite > translategemma 12b (subjective, one grader).',
    rows: TRICKY_ROWS,
  },
  {
    id: 'comic',
    title: 'Comic page picture plus bubble list, 16 pages (2026-11-11)',
    basis: 'one page',
    source: 'baihe/scanlate-vision-test/model-comparison-2026-11-11.md',
    note: 'Chinese and Korean pages, hand-run on the 4080 PC. Time per page was not measured, so rows are ranked by cost. Kae chose Haiku 5.5 with the full page.',
    rankCost: true,
    rows: COMIC_ROWS,
  },
]

const rank = (values: (number | undefined)[]): (number | null)[] =>
  values.map((v) => (v === undefined ? null : 1 + values.filter((o) => o !== undefined && o < v).length))

/** Competition ranks (1 = fastest); equal times share a rank, and a row with no time gets none. */
export function speedRanks(rows: HandRunRow[]): (number | null)[] {
  return rank(rows.map((r) => r.seconds))
}

/** Competition ranks (1 = cheapest); a row with no dollar cost gets none. */
export function costRanks(rows: HandRunRow[]): (number | null)[] {
  return rank(rows.map((r) => r.costUsd))
}
