import { useCallback, useEffect, useState } from 'react'

import { ApiError } from '../../api/client'
import { cancelJob, getJob } from '../../api/jobs'
import { getArtifactInfo } from '../../api/libraryAdmin'
import { useJob, useJobRun } from '../../hooks/useJob'
import { TERMINAL_STATUSES, jobSucceeded } from '../../types/jobs'
import type { ArtifactKind, LibraryArtifactInfo } from '../../types/libraryAdmin'

/**
 * One fixed-id Library admin job (bulk translate, export, backup, database
 * backup). On mount it reattaches to a queued/running run (a 404 means none
 * is running); `start` runs the caller's POST and then polls. With an
 * artifact kind, the newest file's info is loaded on mount and again when a
 * run succeeds (for the download link).
 */
export function useAdminJob(jobId: string, artifact?: ArtifactKind) {
  const [runId, setRun, runKey] = useJobRun()
  const [startError, setStartError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [info, setInfo] = useState<LibraryArtifactInfo | null>(null)

  const loadInfo = useCallback(() => {
    if (!artifact) return
    getArtifactInfo(artifact).then(setInfo, () => setInfo(null))
  }, [artifact])

  useEffect(() => {
    let cancelled = false
    getJob(jobId).then(
      (j) => !cancelled && !TERMINAL_STATUSES.includes(j.status) && setRun(jobId),
      () => undefined, // 404: nothing running.
    )
    loadInfo()
    return () => {
      cancelled = true
    }
  }, [jobId, setRun, loadInfo])

  const { job, error: pollError, done } = useJob(runId, {
    runKey,
    onDone: (j) => {
      if (jobSucceeded(j)) loadInfo()
    },
  })

  const start = useCallback(
    <T>(post: () => Promise<T>): Promise<T | null> => {
      setStartError(null)
      setStarting(true)
      return post().then(
        (r) => {
          setStarting(false)
          setRun(jobId)
          return r
        },
        (e: unknown) => {
          setStarting(false)
          setStartError(e)
          return null
        },
      )
    },
    [jobId, setRun],
  )

  const cancel = useCallback(() => {
    cancelJob(jobId).catch((e: unknown) => setStartError(e instanceof ApiError ? e : null))
  }, [jobId])

  const active = starting || (!!runId && !done && !pollError)
  return { job, pollError, startError, active, done, info, start, cancel, clearError: () => setStartError(null) }
}
