/** A title page's one-button run as the PC queue holds it: reading it until it ends, and its words. */
import { useEffect, useRef, useState } from 'react'

import type { TitleFlowRun } from '../../types/queue'

const POLL_MS = 3000

export const flowActive = (run: TitleFlowRun | null) => run?.state === 'waiting' || run?.state === 'running'

const STEP_NOUN: Record<string, string> = {
  transcribe: 'Transcription',
  names: 'Finding names',
  import: 'Importing the chapters',
  text: 'Preparing the text',
  lines: 'Making lines',
}

// A job's worker process that died (a CUDA DLL, out of memory) leaves only the runner's own sentence about it,
// which tells a person nothing; the job's log on the Jobs page has the cause.
const WORKER_DIED = /exited unexpectedly \(exit code -?\d+\)/i

/** The words beside the button for the run, with `steps` naming each step while it runs. */
export function flowText(run: TitleFlowRun | null, steps: Record<string, string>): string {
  if (!run) return ''
  switch (run.state) {
    case 'waiting': return run.reason || 'Waiting for the item running in the queue'
    case 'running': return steps[run.step ?? ''] ?? 'Working'
    case 'review': return 'Ready for glossary review'
    case 'stopped':
      if (run.reason && WORKER_DIED.test(run.reason)) return `${STEP_NOUN[run.step ?? ''] ?? 'This step'} stopped unexpectedly.`
      return `Stopped: ${run.reason || 'a step did not finish.'}`
    default: return ''
  }
}

/** The stopped step's job, for "See details" on the Jobs page; null while the run goes on or when no job started. */
export const stoppedJob = (run: TitleFlowRun | null): string | null => (run?.state === 'stopped' ? run.job_id ?? null : null)

/**
 * Reads the run on open and every few seconds while it is in line or running. `onMove` is told each
 * time the run moves on (not for a run found already ended), with whether it just left a running state.
 * `again()` reads afresh after a start, since reading stops once a run has ended.
 */
export function useFlowRun(id: number, read: (id: number) => Promise<TitleFlowRun>, onMove: (run: TitleFlowRun, ended: boolean) => void) {
  const [run, setRun] = useState<TitleFlowRun | null>(null)
  const [reads, setReads] = useState(0)
  const seen = useRef('')
  const move = useRef(onMove)
  useEffect(() => { move.current = onMove })

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const next = () => {
      read(id).then((r) => {
        if (cancelled) return
        setRun(r)
        const key = `${r.state}:${r.step ?? ''}`
        const wasActive = /^(waiting|running):/.test(seen.current)
        if (key !== seen.current && (flowActive(r) || wasActive)) move.current(r, wasActive && !flowActive(r))
        seen.current = key
        if (flowActive(r)) timer = setTimeout(next, POLL_MS)
      }, () => {
        if (!cancelled) timer = setTimeout(next, POLL_MS * 2)
      })
    }
    next()
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [id, read, reads])

  return { run, setRun, again: () => setReads((n) => n + 1) }
}
