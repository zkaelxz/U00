// Translates the pages just after the one being read, in the background, so they are typeset when the reader turns the page.
import { useEffect, useRef } from 'react'

import { useInterval } from '../../hooks/useInterval'
import { useHolds } from '../../hooks/useHolds'
import { browserStorage } from '../../hooks/usePersistedState'

import { cancelJob, getJob } from '../../api/jobs'
import { scanlateApi } from '../../api/scanlate'
import type { ComicPageInfo } from '../../types/comic'
import { isActiveStatus, isTerminalStatus } from '../../types/jobs'
import { isReadTranslated } from './comicLogic'

// Waits out a page flick so skimming through a chapter does not start a run per page.
export const READ_AHEAD_DELAY_MS = 2000
const POLL_MS = 4000

// Whether any of the pages ahead still lacks text, and has not been tried this visit.
export function needsReadAhead(ahead: ComicPageInfo[], tried: ReadonlySet<number>): boolean {
  return ahead.some((p) => !p.has_regions && !tried.has(p.id))
}

interface Options {
  titleId: number
  pages: ComicPageInfo[]
  // 1-based page numbers the reader lists, and the 1-based place of the page being read among them.
  visible: number[]
  position: number
  count: number
  // A title with typeset pages is being translated; an untouched one is only started by "Read translated".
  canTypeset: boolean
  onFinished: () => void
}

export function useReadAhead({ titleId, pages, visible, position, count, canTypeset, onFinished }: Options) {
  const readTranslated = isReadTranslated(browserStorage(), titleId)
  const current = position > 0 ? pages[visible[position - 1] - 1] : undefined
  // The reader's page and the few after it.
  const ahead = visible.slice(Math.max(0, position - 1), position + 3).map((n) => pages[n - 1]).filter(Boolean)
  // Without jobs.start every page turn would send a request the server refuses.
  const canStart = useHolds('jobs.start')
  const enabled = canStart && (canTypeset || readTranslated) && current !== undefined
  const page = current
  // A title made with "Read translated" is still being imported: its pages arrive while the reader is open.
  useInterval(onFinished, count === 0 && readTranslated ? 4000 : null, { pauseWhenHidden: true })
  const tried = useRef(new Set<number>())
  const stopped = useRef(false)
  // Bumped when the reader leaves, so a run request answered after that is cancelled instead of tracked.
  const epoch = useRef(0)
  const jobId = useRef<string | null>(null)
  // The job id is the title's one Scanlate id, shared with a manual run: only the run we started (by its start time) is ours to cancel.
  const startedAt = useRef<number | null | undefined>(undefined)
  const finished = useRef(onFinished)
  finished.current = onFinished

  const pageId = page?.id
  const aheadKey = ahead.map((p) => `${p.id}:${p.has_regions ? 1 : 0}`).join(',')
  useEffect(() => {
    if (!enabled || pageId === undefined || stopped.current || !needsReadAhead(ahead, tried.current)) return
    const timer = window.setTimeout(() => {
      for (const p of ahead) tried.current.add(p.id)
      const sentIn = epoch.current
      scanlateApi.run(titleId, { mode: 'missing', read_ahead_from: pageId }).then(
        (started) => {
          if (epoch.current !== sentIn) {
            void cancelJob(started.job_id).catch(() => {})
            return
          }
          jobId.current = started.job_id
          startedAt.current = undefined
          poll(started.job_id, 0)
        },
        // A job (or the import) already running, no monthly cap for a paid engine, no key or no permission:
        // reading goes on without it, and the next page turn or arriving page asks again.
        () => {
          for (const p of ahead) tried.current.delete(p.id)
        },
      )
    }, READ_AHEAD_DELAY_MS)
    return () => window.clearTimeout(timer)
    // `ahead` is covered by aheadKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [titleId, pageId, aheadKey, enabled])

  function poll(id: string, delay = POLL_MS) {
    window.setTimeout(() => {
      if (jobId.current !== id) return
      getJob(id).then(
        (job) => {
          if (startedAt.current === undefined) startedAt.current = job.started_at
          if (isTerminalStatus(job.status) || (startedAt.current !== null && job.started_at !== startedAt.current)) {
            jobId.current = null
            finished.current()
          } else poll(id)
        },
        () => {
          jobId.current = null
        },
      )
    }, delay)
  }

  // Leaving the reader (or the title) ends the look-ahead, and the page that was being translated is left unfinished.
  useEffect(() => {
    const tryAgain = tried.current
    stopped.current = false
    return () => {
      stopped.current = true
      epoch.current += 1
      tryAgain.clear()
      const id = jobId.current
      const mine = startedAt.current
      jobId.current = null
      // The run may have finished and the reader started another since the last poll: cancel only if it is still ours.
      if (id) {
        getJob(id).then(
          (job) => {
            if (isActiveStatus(job.status) && (mine === undefined || job.started_at === mine)) void cancelJob(id).catch(() => {})
          },
          () => {},
        )
      }
    }
  }, [titleId])
}
