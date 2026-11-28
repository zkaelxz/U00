import { useCallback, useEffect, useState } from 'react'

import { getResources } from '../../api/diagnostics'
import type { ResourceReadings } from '../../types/diagnostics'
import { buttonClass } from '../../components/uiClasses'
import { gpuLine } from './libraryParity'

const REFRESH_MS = 30_000

/** The Jobs panel's GPU readings, admin only (the caller checks). Hidden when this PC has no readable GPU. */
export function GpuLine() {
  const [data, setData] = useState<ResourceReadings | null>(null)
  const [failed, setFailed] = useState(false)

  const load = useCallback(() => getResources().then(
    (r) => { setData(r); setFailed(false) },
    () => setFailed(true),
  ), [])

  useEffect(() => {
    load()
    // A hidden tab has nobody to read it, so it skips the poll; the refresh on return catches up.
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void load() }, REFRESH_MS)
    const onVisible = () => { if (document.visibilityState === 'visible') void load() }
    document.addEventListener('visibilitychange', onVisible)
    return () => { window.clearInterval(timer); document.removeEventListener('visibilitychange', onVisible) }
  }, [load])

  const text = gpuLine(data ?? undefined)
  if (!text) return null
  return (
    <p className="page-meta gpu-line" data-testid="gpu-line">
      <span>{text}</span>
      <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => void load()}
        aria-label="Refresh GPU reading" title={failed ? 'Could not refresh. Try again.' : 'Refresh'}>
        {failed ? 'Retry' : '↻'}
      </button>
    </p>
  )
}
