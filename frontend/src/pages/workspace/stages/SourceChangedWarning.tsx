import { useEffect, useState } from 'react'

import { getSourceChanged } from '../../../api/review'
import { routeHref } from '../../../router'
import { sourceChangedText } from './sourceChanged'

// One line while any translated line's source has changed since. It never blocks
// the stage, so a failed read just shows nothing rather than an error banner.
export function SourceChangedWarning({ dramaId, reloadKey = 0 }: { dramaId: number; reloadKey?: number }) {
  const [count, setCount] = useState(0)
  useEffect(() => {
    let cancelled = false
    getSourceChanged(dramaId).then(
      (r) => !cancelled && setCount(r.count),
      () => !cancelled && setCount(0),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloadKey])
  if (count === 0) return null
  return (
    <p className="muted" role="status" data-testid="source-changed-warning">
      {sourceChangedText(count)}.{' '}
      <a href={routeHref({ name: 'drama', id: dramaId, stage: 'review', focus: 'flagged' })}>Review them</a>
    </p>
  )
}
