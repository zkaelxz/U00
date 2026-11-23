// How a Quick video run reads in the list: its status text, which runs show, and when its title can be removed.
import { withoutExceptionNames } from '../../components/errorMessages'
import type { QuickVideoRun, QuickVideoStep } from '../../types/quickVideo'

// Eight finished runs pushed the form nearly two screens down on a phone.
const SHOWN_RUNS = 3
const STEP_NOW: Record<QuickVideoStep, string> = {
  download: 'Downloading', transcribe: 'Transcribing', translate: 'Translating', export: 'Exporting',
}
const STEP_NAME: Record<QuickVideoStep, string> = {
  download: 'Download', transcribe: 'Transcribe', translate: 'Translate', export: 'Export',
}

export const runText = (run: QuickVideoRun): string =>
  run.state === 'done' ? 'Done'
    : run.state === 'stopped' ? `${STEP_NAME[run.step]} stopped${run.reason ? `: ${withoutExceptionNames(run.reason)}` : ''}`
      : STEP_NOW[run.step]

export const visibleRuns = (runs: QuickVideoRun[], all: boolean): QuickVideoRun[] =>
  all ? runs : runs.filter((r, i) => i < SHOWN_RUNS || r.state === 'running')

// A failed or stopped run has nothing to finish watching.
export const canDeleteWatched = (run: QuickVideoRun): boolean => run.watch_once && run.state === 'done'
