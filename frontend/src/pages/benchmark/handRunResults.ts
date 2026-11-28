/** Hand-run model comparisons copied from the Library write-ups and the results ledger; not data the app recorded. */
import { bestValueIndex, competitionRanks } from './runRanking'

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
  /** What reading the output showed. */
  reading: string
  /** A real score (chrF, 0-100, higher is better); when the test has scores they set the quality rank. */
  score?: number
  /** Quality tier from the written reading (1 = best, equal tiers share a rank); used only where there is no score. */
  qualityTier?: number
  /** Why this row no longer reflects what the app does; the row stays, marked. */
  outdated?: string
}

export interface HandRunTest {
  id: string
  title: string
  /** When the test ran, as written in its source. */
  date: string
  /** Why the whole test is older than the models and defaults in use now. */
  outdated?: string
  /** Name of the real score the rows carry, for the column heading. */
  scoreName?: string
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
  { model: 'Sonnet 5.5 (thinking on, app default)', qualityTier: 1, seconds: 27.5, costUsd: 0.0501, reading: 'Best on idioms and the nickname.' },
  { model: 'Sonnet 5.5, thinking off', qualityTier: 2, seconds: 12.5, costUsd: 0.03, reading: 'Slightly worse on slang and the nickname.' },
  { model: 'GPT-6 Luna (default)', qualityTier: 2, seconds: 66.7, costUsd: 0.0038, reading: 'About as good as low; read the drunk slang right once.' },
  { model: 'GPT-6 Luna, low reasoning', qualityTier: 3, seconds: 23.9, costUsd: 0.0015, reading: 'About the same as default; lost that slang line.' },
  { model: 'GPT-6 Luna, low reasoning + glossary fixes', qualityTier: 2, seconds: 25.1, costUsd: 0.00153, reading: 'Fixed the nickname and the drunk slang; the glossary helped more than extra reasoning.' },
  { model: 'GPT-6 Luna, none reasoning', qualityTier: 3, seconds: 14.6, costUsd: 0.001, reading: 'About the same size of mistakes; one line wrong.' },
  { model: 'GPT-6.1 Terra, low reasoning', qualityTier: 3, seconds: 22.7, costUsd: 0.012, reading: 'Small step up from Luna low on idioms, about 8x its cost.' },
  { model: 'DeepSeek flash (thinking off, app default)', qualityTier: 4, seconds: 8.9, costUsd: 0.0028, reading: 'Literal; got the idiom and slang wrong.' },
  { model: 'DeepSeek flash, thinking on', qualityTier: 3, seconds: 44.0, costUsd: 0.0105, reading: 'More consistent nickname; about 3.7x the cost for a small gain.' },
  { model: 'Gemini 3.8 Flash (default)', qualityTier: 3, seconds: 70.7, costUsd: 0.0415, reading: 'Got the idiom right; added a gender to the nickname.' },
  { model: 'Gemini 3.8 Flash, low', qualityTier: 4, seconds: 22.9, costUsd: 0.0152, reading: 'About 3x cheaper, but lost the idiom and invented an insult.' },
  { model: 'Gemini 3.5 Flash-Lite (default)', qualityTier: 4, seconds: 6.1, costUsd: 0.0064, reading: 'Fine for the price; name drift.' },
  { model: 'gemma4:31b-cloud', qualityTier: 4, seconds: 13.1, costUsd: 0.0017, costEstimated: true, reading: 'Natural; a notch below Luna on slang.' },
  { model: 'translategemma:27b (local, V100)', qualityTier: 5, seconds: 67.6, costUsd: 0, reading: 'Usable gist for free; clearly behind the cloud models on slang and context.' },
]

const TRICKY_ROWS: HandRunRow[] = [
  { model: 'Sonnet 5.5', qualityTier: 1, seconds: 3.5, reading: 'Most natural and faithful to register; numbers 8/8. In plain mode it added a stray "(Teaching a fish to swim.)" to one line.' },
  { model: 'Gemini 3.8 Flash', qualityTier: 1, seconds: 18.7, costUsd: 0.0138, reading: 'As good as Sonnet on this set; numbers 8/8. Thinks by default, so about 10x the flash-lite cost.' },
  { model: 'DeepSeek flash', qualityTier: 2, seconds: 8.5, timeLabel: '7-10 s', reading: 'Close behind Sonnet; about 3x the output tokens; writes "Qingyun" without a glossary; numbers 8/8.' },
  { model: 'Gemini 3.1 Flash-Lite', qualityTier: 2, seconds: 1.5, timeLabel: '~1.5 s', costUsd: 0.0008, reading: 'Best value; one literal idiom ("fishes three days, dries his nets two"); numbers 8/8.' },
  { model: 'Gemini 3.5 Flash-Lite', qualityTier: 3, seconds: 1.5, timeLabel: '~1.5 s', costUsd: 0.0013, reading: 'Weakest of the three Gemini models: "Cut the crap" is too vulgar for the scene, one idiom off; numbers 8/8.' },
  { model: 'gemma4:31b-cloud', qualityTier: 3, seconds: 1.1, reading: 'Fastest and mostly good; slips on two idioms; numbers 8/8.' },
  { model: 'GPT-6 Luna', qualityTier: 2, seconds: 6.3, timeLabel: '5.1-7.5 s', reading: 'Keeps the fishing-and-nets idiom and the dead-back-to-life joke; numbers 8/8; writes "Qingyun" without a glossary.' },
  { model: 'GPT-6.1 Terra', qualityTier: 3, seconds: 6.3, timeLabel: '5.9-6.6 s', reading: 'Flattens one idiom and drops a subject; numbers 8/8.' },
  { model: 'translategemma:27b (local, V100)', qualityTier: 3, reading: 'Gets the three idiom lines (3, 4 and 12); numbers 8/8. Time for this request was not measured (about 1.04 s per cue in the Live-style timing).' },
  { model: 'translategemma:12b (local, V100)', qualityTier: 4, seconds: 23, timeLabel: '12-34 s', reading: 'Best small local model on tone; missed one idiom; numbers 6/8 (25,000 became "Two thousand five hundred", 400 li became "400 miles").' },
  { model: 'translategemma:4b (local, V100)', qualityTier: 5, seconds: 18, timeLabel: '17-19 s', reading: 'Most literal; the worst idiom errors. Numbers were not tested.' },
]

const COMIC_ROWS: HandRunRow[] = [
  { model: 'Haiku 5.5, crops only', outdated: 'The comic picture reader is now gemma4:31b-cloud, with Haiku only as the fallback.', qualityTier: 3, costUsd: 0.0002, reading: 'Matched full-page dialogue, but one crop returned a picture description instead of a translation.' },
  { model: 'Haiku 5.5, full page (the app\'s picture model then)', outdated: 'The comic picture reader is now gemma4:31b-cloud, with Haiku only as the fallback.', qualityTier: 2, costUsd: 0.0006, reading: 'Dialogue as good as Sonnet 5.5 on these pages at about a fifteenth of the cost.' },
  { model: 'Gemini 3.1 Flash-Lite, page image', qualityTier: 2, costUsd: 0.0006, reading: 'No dropped lines; on par with Haiku in a spot-check of fewer lines; same caption mistake as text-only Sonnet.' },
  { model: 'GPT-6 Luna, page image', qualityTier: 3, costUsd: 0.0004, costEstimated: true, reading: 'Read well, but translated a watermark line. Price was unconfirmed at the time.' },
  { model: 'Gemini Flash-Lite (older), page image', qualityTier: 3, costUsd: 0.0008, reading: 'Dropped one line.' },
  { model: 'Sonnet 5.5, text only', qualityTier: 3, costUsd: 0.0023, reading: 'Dialogue about equal to the picture arms; worse on garbled OCR, sound effects, watermarks and tone.' },
  { model: 'Gemini Flash, page image', qualityTier: 2, costUsd: 0.0043, reading: 'Not better than Haiku and 7 times the cost.' },
  { model: 'Sonnet 5.5, page image', qualityTier: 1, costUsd: 0.0087, reading: 'Good, and better than text-only on sound effects, watermarks and tone.' },
  { model: 'Opus 5.5, page image', costLabel: 'plan credit', reading: 'Run on plan credit, so no API cost.' },
]

const TITLE79_ROWS: HandRunRow[] = [
  { model: 'gemma4:31b-cloud', score: 63.1, costUsd: 0.14, reading: 'Highest chrF and most exact lines (85 of 471), but the most lines the judge called changed in meaning (95); 15 lines not judged.' },
  { model: 'DeepSeek flash', score: 61.1, costUsd: 0.12, reading: 'Cheapest; 81 exact; the most wrong-subject lines (19), judge same 370, changed 82.' },
  { model: 'Sonnet 5.5', score: 60.1, costUsd: 0.34, reading: '82 exact; judge same 375 (the most), changed 81, wrong subject 15. About 2.5x the cost of the others.' },
  { model: 'GPT-6 Luna', score: 59.7, costUsd: 0.13, reading: '79 exact; judge same 361, changed 98, wrong subject 12.' },
]

const NOVEL_ROWS: HandRunRow[] = [
  { model: 'gemma4:31b-cloud', score: 75.63, seconds: 154, costUsd: 0.024, reading: 'Best chrF; 0 empty lines. In the owner\'s blind read of 16 passages: gemma 4 wins, DeepSeek 3, Sonnet 2, 7 about the same.' },
  { model: 'DeepSeek flash', score: 73.85, seconds: 150, costUsd: 0.0425, reading: 'About level with Sonnet; 1 empty line in one run. Per-batch speed about 0.19 s per line.' },
  { model: 'Sonnet 5.5', score: 73.65, seconds: 218.5, costUsd: 0.4, reading: 'About level with DeepSeek at roughly 10x its cost and the slowest.' },
  { model: 'Haiku 5.5', score: 68.64, seconds: 167, costUsd: 0.0265, reading: 'Fails: drops whole 30-line blocks (60 empty lines per run), the lowest chrF.' },
]

export const HAND_RUN_TESTS: HandRunTest[] = [
  {
    id: 'title79',
    title: 'Subtitle gold title 79, 471 hand-fixed lines',
    date: '2026-11-28',
    scoreName: 'chrF',
    basis: '471 lines',
    source: 'model-results.md, 11-28 "Gold tools step 2" (the translation gold bench, Claude as judge, one run each)',
    note: 'Scored against the owner\'s hand-fixed lines with chrF. One run each: gaps of about a point or two are within run-to-run noise, so the order is a first look, not a verdict.',
    rows: TITLE79_ROWS,
  },
  {
    id: 'novel',
    title: 'Modern novel, 4 chapters (s06-s09)',
    date: '2026-11-28',
    scoreName: 'chrF',
    basis: '4 chapters',
    source: 'model-results.md, 11-28 "Novel model check" run 1 and the H arm (two runs each, app defaults; time and cost averaged over the runs)',
    note: 'Scored against the book\'s reference text with chrF; for the first three models the two runs of an arm differed by 0.16 or less. Haiku\'s time and cost are from a test copy that allowed it as a translation model.',
    rows: NOVEL_ROWS,
  },
  {
    id: 'stream',
    title: 'Mandarin stream chunk, 90 lines',
    date: '2026-11-11',
    outdated: "Older than the models and defaults in use now: the app's defaults and several models have changed since, so read it as a snapshot of 11-11. Newer scored results are listed first.",
    basis: '90 lines',
    source: 'baihe/translation-bench-v100/stream-test/stream-test-2026-11-11.md and thinking-on-off-2026-11-11.md',
    note: 'The app\'s own translate pipeline on a drunk-stream transcript. Gemini 3.1 Flash-Lite is left out: it returned nothing for the second batch of 30 lines in every run, so it is unrated here.',
    scaleTo2000Lines: true,
    rows: STREAM_ROWS,
  },
  {
    id: 'tricky',
    title: 'Tricky wuxia lines (16) and number lines (8)',
    date: '2026-11-11',
    outdated: "Older than the models and defaults in use now: the app's defaults and several models have changed since, so read it as a snapshot of 11-11. Newer scored results are listed first.",
    basis: 'one 16-line request',
    source: 'baihe/translation-bench-v100/tricky-all-models-2026-11-11.md and gemini-tricky-2026-11-11.md (top level of the Library)',
    note: 'Costs are only given for the Gemini rows (tricky plus number lines). Ranges are ranked on their midpoint. Tester\'s overall order for the models it compared: Sonnet 5.5 >= Gemini 3.8 Flash > DeepSeek flash ~ Gemini 3.1 Flash-Lite > Gemini 3.5 Flash-Lite > translategemma 12b (subjective, one grader).',
    rows: TRICKY_ROWS,
  },
  {
    id: 'comic',
    title: 'Comic page picture plus bubble list, 16 pages',
    date: '2026-11-11',
    outdated: "Older than the models and defaults in use now: the app's defaults and several models have changed since, so read it as a snapshot of 11-11. Newer scored results are listed first.",
    basis: 'one page',
    source: 'baihe/scanlate-vision-test/model-comparison-2026-11-11.md',
    note: 'Chinese and Korean pages, hand-run on the 4080 PC. Time per page was not measured, so rows are ranked by cost. The owner chose Haiku 5.5 with the full page.',
    rankCost: true,
    rows: COMIC_ROWS,
  },
]

const rank = (values: (number | undefined)[]): (number | null)[] => competitionRanks(values, false)

/** Competition ranks (1 = fastest); equal times share a rank, and a row with no time gets none. */
export function speedRanks(rows: HandRunRow[]): (number | null)[] {
  return rank(rows.map((r) => r.seconds))
}

/** Competition ranks (1 = cheapest); a row with no dollar cost gets none. */
export function costRanks(rows: HandRunRow[]): (number | null)[] {
  return rank(rows.map((r) => r.costUsd))
}

/** Row quality in the test's own unit: the score where rows have one, else the reading tier flipped so higher is better. */
export interface QualityFigure {
  /** Competition ranks, 1 = best; null for a row with nothing to rank. */
  ranks: (number | null)[]
  /** True when the ranks come from the written reading, not a score. */
  judged: boolean
  /** Higher-is-better figure per row, for the value hint. */
  figures: (number | null)[]
}

export function qualityRanks(test: HandRunTest): QualityFigure {
  const scored = test.rows.some((r) => r.score !== undefined)
  if (scored) {
    const figures = test.rows.map((r) => r.score ?? null)
    return { ranks: competitionRanks(figures, true), judged: false, figures }
  }
  const worst = Math.max(0, ...test.rows.map((r) => r.qualityTier ?? 0))
  const figures = test.rows.map((r) => (r.qualityTier === undefined ? null : worst + 1 - r.qualityTier))
  return { ranks: competitionRanks(figures, true), judged: true, figures }
}

/** chrF points (or reading tiers) within which a row still counts as top quality for the value hint. */
const SCORE_VALUE_TOLERANCE = 2
const TIER_VALUE_TOLERANCE = 1

/** Index of the best-value row (most quality per dollar among the top-quality rows), or -1. */
export function bestValueRow(test: HandRunTest): number {
  const { figures, judged } = qualityRanks(test)
  return bestValueIndex(test.rows.map((r, i) => ({ quality: figures[i], cost: r.costUsd ?? null })), judged ? TIER_VALUE_TOLERANCE : SCORE_VALUE_TOLERANCE)
}
