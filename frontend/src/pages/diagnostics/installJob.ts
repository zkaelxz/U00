// Following the package install / GPU PyTorch setup job: the page starts it,
// then polls its state until it ends. Pure of React so it is unit-tested.
import { getDependencyInstall } from '../../api/diagnosticsInstalls'
import type {
  DiagnosticsDependencyInstallResult, DiagnosticsJobStarted, DiagnosticsJobState,
} from '../../types/diagnosticsInstalls'
import { JOB_POLL_MS, jobRunning } from './jobPoll'

// A dropped connection is retried this many polls in a row before giving up;
// the job keeps running on the PC either way.
const MAX_POLL_FAILURES = 5

type Wait = (ms: number) => Promise<void>
const sleep: Wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/** What the page shows when the job ended without a stored result (cleared record, crash). */
function resultFromJob(job: DiagnosticsJobState | null, name: string): DiagnosticsDependencyInstallResult {
  return {
    package: name, ok: false, output_tail: [], hint: job?.error ?? null,
    cancelled: job?.status === 'cancelled', variant: null, verify: null,
  }
}

/** Polls until the job ends; `onJob` sees each running state (progress and message). */
export async function followInstallJob(
  onJob?: (job: DiagnosticsJobState) => void, name = '', pollMs = JOB_POLL_MS, wait: Wait = sleep,
): Promise<DiagnosticsDependencyInstallResult> {
  let failures = 0
  for (;;) {
    let state
    try {
      state = await getDependencyInstall()
      failures = 0
    } catch (e) {
      failures += 1
      if (failures >= MAX_POLL_FAILURES) throw e
      await wait(pollMs)
      continue
    }
    if (state.job && jobRunning(state.job)) {
      onJob?.(state.job)
      await wait(pollMs)
      continue
    }
    return state.result ?? resultFromJob(state.job, state.package ?? name)
  }
}

/** Starts the job (a refusal throws, like the old synchronous call), then follows it to its end. */
export async function runInstallJob(
  start: () => Promise<DiagnosticsJobStarted>, onJob?: (job: DiagnosticsJobState) => void, name = '',
  pollMs = JOB_POLL_MS, wait: Wait = sleep,
): Promise<DiagnosticsDependencyInstallResult> {
  await start()
  return followInstallJob(onJob, name, pollMs, wait)
}

export const cancelledText = (name: string) => `Cancelled installing ${name}.`
