/* The followed-channels list: loaded once for the Live page and re-read while a check runs, so the live-now cards and the channel rows share it. */
import { useCallback, useEffect, useRef, useState } from 'react'

import { describeFollowError, listFollows } from '../../api/liveFollows'
import type { LiveFollowList } from '../../types/live'

// While a check is queued or running, the list is re-read this often.
const CHECKING_POLL_MS = 3000

export function useFollows() {
  const [list, setList] = useState<LiveFollowList | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const alive = useRef(true)

  const load = useCallback(
    () =>
      listFollows().then(
        (r) => {
          if (!alive.current) return null
          setList(r)
          setLoadError(null)
          return r
        },
        (err: unknown) => {
          if (alive.current) setLoadError(describeFollowError(err))
          return null
        },
      ),
    [],
  )

  useEffect(() => {
    alive.current = true
    void load()
    return () => {
      alive.current = false
    }
  }, [load])

  const anyChecking = !!list?.items.some((f) => f.checking)
  useEffect(() => {
    if (!anyChecking) return
    const timer = window.setTimeout(() => void load(), CHECKING_POLL_MS)
    return () => window.clearTimeout(timer)
  }, [anyChecking, list, load])

  return { list, loadError, load, alive, anyChecking }
}

export type Follows = ReturnType<typeof useFollows>
