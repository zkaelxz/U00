import { useCallback, useEffect, useState } from 'react'

import type { MediaKind } from '../../../api/media'
import { getMediaStatus } from '../../../api/workspace'
import { useStage } from '../StageContext'
import { AiExtras } from './review/AiExtras'
import { LinesPanel } from './review/LinesPanel'
import { RecordsPanel } from './review/RecordsPanel'
import { ReviewChecks } from './review/ReviewChecks'
import { ReviewJobsPanel } from './review/ReviewJobsPanel'
import { StructureSection } from './review/StructureSection'
import type { LineTarget } from './review/reviewResults'
import { useDramaJobRunning } from './review/useDramaJobRunning'
import './review/review.css'

export default function ReviewStage() {
  const { dramaId, drama } = useStage()
  // Bumped after any write or finished job; every panel refetches on it.
  const [reloads, setReloads] = useState(0)
  const changed = useCallback(() => setReloads((n) => n + 1), [])
  const jobRunning = useDramaJobRunning(dramaId, reloads)
  const [lineCount, setLineCount] = useState<number | null>(null)
  // A finding's line link: the editor opens that line (by id where known).
  const [goTo, setGoTo] = useState<{ target: LineTarget; seq: number; resolve: (m: string | null) => void } | null>(null)
  const goToLine = useCallback(
    (target: LineTarget) =>
      new Promise<string | null>((resolve) => setGoTo((g) => ({ target, seq: (g?.seq ?? 0) + 1, resolve }))),
    [],
  )
  const [flaggedCount, setFlaggedCount] = useState<number | null>(null)

  // Play from the source video when there is one (its sound plays even while
  // the picture is folded away), else from the audio; no media, no player.
  const [mediaKind, setMediaKind] = useState<MediaKind | null>(null)
  useEffect(() => {
    let cancelled = false
    getMediaStatus(dramaId).then(
      (s) => !cancelled && setMediaKind(s.has_source_video ? 'video' : s.has_audio ? 'audio' : null),
      () => !cancelled && setMediaKind(drama.has_audio ? 'audio' : null),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, drama.has_audio])

  return (
    <div className="stage-review" role="region" aria-label="Review">
      <LinesPanel
        dramaId={dramaId}
        reloads={reloads}
        onChanged={changed}
        jobRunning={jobRunning}
        mediaKind={mediaKind}
        onLineCount={setLineCount}
        onFlaggedCount={setFlaggedCount}
        goTo={goTo}
      />
      <ReviewJobsPanel dramaId={dramaId} reloads={reloads} onChanged={changed} onGoTo={goToLine} flaggedCount={flaggedCount} />
      {!!lineCount && <StructureSection dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />}
      {!!lineCount && <ReviewChecks dramaId={dramaId} reloads={reloads} onGoTo={goToLine} onChanged={changed} jobRunning={jobRunning} />}
      {!!lineCount && <AiExtras dramaId={dramaId} reloads={reloads} jobRunning={jobRunning} onChanged={changed} />}
      <RecordsPanel dramaId={dramaId} reloads={reloads} onChanged={changed} jobRunning={jobRunning} onGoTo={goToLine} />
    </div>
  )
}
