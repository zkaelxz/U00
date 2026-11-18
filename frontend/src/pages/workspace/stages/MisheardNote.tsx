import { useEffect, useState } from 'react'

import { listMisheard } from '../../../api/misheard'
import { routeHref } from '../../../router'
import { useStage } from '../StageContext'

// Transcribe: how many words in a Chinese title may have been mis-heard, with a link to the
// list in Review. Only counts; nothing is changed here. A failed or unavailable check shows nothing.
export function MisheardNote({ busy }: { busy: boolean }) {
  const { dramaId } = useStage()
  const [count, setCount] = useState(0)
  useEffect(() => {
    if (busy) return
    let live = true
    listMisheard(dramaId).then(
      (r) => live && setCount(r.available ? r.suggestions.length : 0),
      () => live && setCount(0),
    )
    return () => {
      live = false
    }
  }, [dramaId, busy])
  if (count === 0) return null
  const href = routeHref({ name: 'drama', id: dramaId, stage: 'review' })
  return (
    <p className="muted" data-testid="misheard-note">
      {count} {count === 1 ? 'word' : 'words'} may be mis-heard.{' '}
      <a href={href}>Check them in Review</a> (press the source-text chip).
    </p>
  )
}
