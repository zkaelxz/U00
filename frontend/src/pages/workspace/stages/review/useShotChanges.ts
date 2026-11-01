// The shot changes found for the waveform: asked for the window on screen, kept while the viewer pans.
import { useCallback, useEffect, useRef, useState } from 'react'

import { getShotChanges } from '../../../../api/media'
import { describeError } from '../../../../components/errorMessages'
import { mergeCuts, round3, type View } from './waveformLogic'

export function useShotChanges(dramaId: number) {
  // Tagged with its title: another title's cuts must not show here.
  const [found, setFound] = useState<{ dramaId: number; cuts: number[] }>({ dramaId, cuts: [] })
  const cuts = found.dramaId === dramaId ? found.cuts : []
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ctl = useRef<AbortController | null>(null)

  useEffect(() => () => ctl.current?.abort(), [])

  const find = useCallback(async (view: View) => {
    ctl.current?.abort()
    const c = new AbortController()
    ctl.current = c
    setBusy(true)
    setError(null)
    try {
      const r = await getShotChanges(dramaId, round3(view.start), round3(view.start + view.span), c.signal)
      if (!c.signal.aborted) setFound((cur) => ({ dramaId, cuts: mergeCuts(cur.dramaId === dramaId ? cur.cuts : [], r.cuts) }))
    } catch (e) {
      if (!c.signal.aborted) setError(describeError(e).title)
    } finally {
      if (!c.signal.aborted) setBusy(false)
    }
  }, [dramaId])

  return { cuts, busy, error, find }
}
