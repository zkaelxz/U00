// Translates the pages just after the one being read, in the background, so they are typeset when the reader turns the page.
import { useEffect, useRef } from 'react'

import { cancelJob, getJob } from '../../api/jobs'
import { scanlateApi } from '../../api/scanlate'
import type { ComicPageInfo } from '../../types/comic'
import { isTerminalStatus } from '../../types/jobs'

// Waits out a page flick so skimming through a chapter does not start a run per page.
export const READ_AHEAD_DELAY_MS = 2000
const POLL_MS = 4000

// Whether any of the pages ahead still lacks text, and has not been tried this visit.
export function needsReadAhead(ahead: ComicPageInfo[], tried: ReadonlySet<number>): boolean {
  return ahead.some((p) => !p.has_regions && !tried.has(p.id))
}

interface Options {
  titleId: number
  // The page being read, and the few visible pages after it.
  page: ComicPageInfo | undefined
  ahead: ComicPageInfo[]
  // Only a title that already has typeset pages is being translated; an untouched one is never started.
  enabled: boolean
  onFinished: () => void
}

export function useReadAhead({ titleId, page, ahead, enabled, onFinished }: Options) {
  const tried = useRef(new Set<number>())
  const stopped = useRef(false)
  const jobId = useRef<string | null>(null)
  const finished = useRef(onFinished)
  finished.current = onFinished

  const pageId = page?.id
  const aheadKey = ahead.map((p) => `${p.id}:${p.has_regions ? 1 : 0}`).join(',')
  useEffect(() => {
    if (!enabled || pageId === undefined || stopped.current || !needsReadAhead(ahead, tried.current)) return
    const timer = window.setTimeout(() => {
      for (const p of ahead) tried.current.add(p.id)
      scanlateApi.run(titleId, { mode: 'missing', read_ahead_from: pageId }).then(
        (started) => {
          jobId.current = started.job_id
          poll(started.job_id)
        },
        // A job already running, no monthly cap for a paid engine, no key or no permission: reading goes on without it.
        () => {},
      )
    }, READ_AHEAD_DELAY_MS)
    return () => window.clearTimeout(timer)
    // `ahead` is covered by aheadKey.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [titleId, pageId, aheadKey, enabled])

  function poll(id: string) {
    window.setTimeout(() => {
      if (jobId.current !== id) return
      getJob(id).then(
        (job) => {
          if (isTerminalStatus(job.status)) {
            jobId.current = null
            finished.current()
          } else poll(id)
        },
        () => {
          jobId.current = null
        },
      )
    }, POLL_MS)
  }

  // Leaving the reader (or the title) ends the look-ahead, and the page that was being translated is left unfinished.
  useEffect(() => {
    const tryAgain = tried.current
    stopped.current = false
    return () => {
      stopped.current = true
      tryAgain.clear()
      const id = jobId.current
      jobId.current = null
      if (id) void cancelJob(id).catch(() => {})
    }
  }, [titleId])
}
