import { useCallback, useState } from 'react'

import { useStage } from '../StageContext'
import { FindReplacePanel } from './review/FindReplacePanel'
import { LinesPanel } from './review/LinesPanel'
import { RecordsPanel } from './review/RecordsPanel'
import { ReviewJobsPanel } from './review/ReviewJobsPanel'
import './review/review.css'

export default function ReviewStage() {
  const { dramaId } = useStage()
  // Bumped after any write or finished job; every panel refetches on it.
  const [reloads, setReloads] = useState(0)
  const changed = useCallback(() => setReloads((n) => n + 1), [])

  return (
    <div className="stage-review" role="region" aria-label="Review">
      <ReviewJobsPanel dramaId={dramaId} onChanged={changed} />
      <LinesPanel dramaId={dramaId} reloads={reloads} onChanged={changed} />
      <FindReplacePanel dramaId={dramaId} onChanged={changed} />
      <RecordsPanel dramaId={dramaId} reloads={reloads} onChanged={changed} />
    </div>
  )
}
