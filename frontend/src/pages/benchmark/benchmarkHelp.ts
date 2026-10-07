// All the explanatory copy of the Benchmark Lab page, in one place so it is easy to edit.
// Keep each line to about a dozen plain words.

export const BENCH_INTRO: string[] = [
  'Check whether an engine, model or prompt translates better before you switch.',
  'It scores their output against golden sets: lines with known-good reference answers.',
  'Every run is saved with its score and cost.',
  'Your titles and lines are never touched. Only promoting a model changes a setting.',
]

export type BenchSectionId = 'sets' | 'run' | 'reeval' | 'runs'

type SectionCopy = {
  title: string
  purpose: string
  // The (i) help: numbered steps, in order.
  steps: string[]
}

export const BENCH_SECTIONS: Record<BenchSectionId, SectionCopy> = {
  sets: {
    title: 'Golden sets',
    purpose: 'The reference translations that runs are scored against.',
    steps: [
      'Import a set (JSONL or TSV), or add one case by hand.',
      'A case is a source line plus its reference translation.',
      'A case without a reference is run but not scored.',
      'Show cases to read a set or delete a case.',
      'Importing, adding and deleting happen on the main PC only.',
    ],
  },
  run: {
    title: 'Run a benchmark',
    purpose: 'Send a golden set through one or more engines and score the output.',
    steps: [
      'Pick the stage, tier and golden set.',
      'Pick an engine and model. Add more to compare them.',
      'Press Estimate cost. Start unlocks once the estimate is shown.',
      'Press Start. Progress shows here while it runs.',
      'Read the scores under Recent runs.',
    ],
  },
  reeval: {
    title: 'Model re-evaluation',
    purpose: 'Check whether a newer model beats the one you use now.',
    steps: [
      'Production is the model you use now.',
      'Add up to 3 candidates: models you want to try.',
      'Run now scores production and every candidate on a golden set.',
      'Read the Latest report. Differences are candidate minus production.',
      'Promote makes a candidate production, after you confirm.',
      'Promote also changes your default engine if its engine differs.',
      'Reject leaves a candidate out of later runs.',
      'The schedule is off by default. Nothing changes by itself.',
    ],
  },
  runs: {
    title: 'Recent runs',
    purpose: 'Every past run with its score, cost and time.',
    steps: [
      'Score is how close the output is to the reference. Higher is better.',
      'Results shows one run, case by case.',
      'Tick two or more runs, then Compare in Arena.',
      'Engines started together as an arena have a one-click Compare.',
    ],
  },
}

// Open on first visit: the golden set is the first thing a new user needs.
export const BENCH_DEFAULT_OPEN: Record<BenchSectionId, boolean> = { sets: true, run: false, reeval: false, runs: false }

export const NO_CASES_HINT = 'No cases match yet. Import a golden set above, or widen Tier and Golden set.'
