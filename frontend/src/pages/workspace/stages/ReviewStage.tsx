import { useCallback, useEffect, useState, type ReactNode } from 'react'

import type { MediaKind } from '../../../api/media'
import { getMediaStatus } from '../../../api/workspace'
import { useStage } from '../StageContext'
import { Section } from '../../../components/Section'
import { readSectionOpen } from '../../../components/sectionStorage'
import { AiExtrasBurnPreview } from './review/AiExtrasBurnPreview'
import { AiExtrasMerge } from './review/AiExtrasMerge'
import { AiExtrasSenseVoice } from './review/AiExtrasSenseVoice'
import { AiExtrasStyle } from './review/AiExtrasStyle'
import { LinesPanel } from './review/LinesPanel'
import { RecordsPanel } from './review/RecordsPanel'
import { ReviewChecks } from './review/ReviewChecks'
import { ReviewFlags } from './review/ReviewFlags'
import { ReviewJobsPanel } from './review/ReviewJobsPanel'
import { ResplitLines } from './review/ResplitLines'
import { StructureSection } from './review/StructureSection'
import type { LineTarget } from './review/reviewResults'
import { useDramaJobRunning } from './review/useDramaJobRunning'
import './review/review.css'

// A top-level fold of the stage. The body is handed `opened` (true once the
// fold has been opened, or was remembered open) so heavy parts can wait.
function Fold({ storageKey, title, summary, children }: {
  storageKey: string
  title: string
  summary: string
  children: (opened: boolean) => ReactNode
}) {
  const [opened, setOpened] = useState(() => {
    try {
      return readSectionOpen(window.localStorage, storageKey, false)
    } catch {
      return false
    }
  })
  return (
    <Section storageKey={storageKey} title={title} summary={summary} onToggle={(open) => open && setOpened(true)}>
      <div className="stack">{children(opened)}</div>
    </Section>
  )
}

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
      <ReviewChecks
        enabled={!!lineCount}
        dramaId={dramaId}
        reloads={reloads}
        onGoTo={goToLine}
        onChanged={changed}
        jobRunning={jobRunning}
        render={(parts) => (
          <>
            <ReviewJobsPanel dramaId={dramaId} reloads={reloads} onChanged={changed} onGoTo={goToLine} flaggedCount={flaggedCount}>
              {!!lineCount && <ReviewFlags onDone={changed} />}
              {parts.coverage}
            </ReviewJobsPanel>
            {!!lineCount && (
              <Fold storageKey="review.fold.restructure" title="Restructure lines" summary="Structure · re-split · merge short · shorten overlong">
                {() => (
                  <>
                    <StructureSection dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />
                    <ResplitLines dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />
                    <AiExtrasMerge dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />
                    {parts.shorten}
                  </>
                )}
              </Fold>
            )}
            <Fold storageKey="review.fold.history" title="Versions and history" summary="Notes · versions · history · compare · edit tendencies">
              {(opened) => (
                <>
                  <RecordsPanel dramaId={dramaId} reloads={reloads} onChanged={changed} jobRunning={jobRunning} onGoTo={goToLine} />
                  {parts.history}
                  {!!lineCount && opened && <AiExtrasStyle dramaId={dramaId} reloads={reloads} />}
                </>
              )}
            </Fold>
            {!!lineCount && (
              <Fold storageKey="review.fold.extras" title="Extras" summary="Audio tags · burned preview">
                {(opened) =>
                  opened && (
                    <>
                      <AiExtrasSenseVoice dramaId={dramaId} reloads={reloads} />
                      <AiExtrasBurnPreview dramaId={dramaId} />
                    </>
                  )
                }
              </Fold>
            )}
          </>
        )}
      />
    </div>
  )
}
