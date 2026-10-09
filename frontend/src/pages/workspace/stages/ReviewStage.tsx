import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'

import type { MediaKind } from '../../../api/media'
import { getMediaStatus } from '../../../api/workspace'
import { useStage } from '../StageContext'
import { Section } from '../../../components/Section'
import { readSectionOpen } from '../../../components/sectionStorage'
import { AiExtrasBurnPreview } from './review/AiExtrasBurnPreview'
import { AiExtrasCleanup } from './review/AiExtrasCleanup'
import { AiExtrasMerge } from './review/AiExtrasMerge'
import { AiExtrasSenseVoice } from './review/AiExtrasSenseVoice'
import { AiExtrasStyle } from './review/AiExtrasStyle'
import { CompareTranscription } from './review/CompareTranscription'
import { RetimeLines } from './review/RetimeLines'
import { RetranscribeLines, type RetranscribeRequest } from './review/RetranscribeLines'
import { LinesPanel } from './review/LinesPanel'
import { LineSelectionProvider } from './review/LineSelectionContext'
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
function Fold({ storageKey, title, summary, openSignal, children }: {
  storageKey: string
  title: string
  summary: string
  // Each change opens the fold, as Section's openSignal does, and counts as opened.
  openSignal?: number
  children: (opened: boolean) => ReactNode
}) {
  const [opened, setOpened] = useState(() => {
    try {
      return readSectionOpen(window.localStorage, storageKey, false)
    } catch {
      return false
    }
  })
  const [seenSignal, setSeenSignal] = useState(openSignal)
  if (seenSignal !== openSignal) {
    setSeenSignal(openSignal)
    setOpened(true)
  }
  return (
    <Section storageKey={storageKey} title={title} summary={summary} openSignal={openSignal} onToggle={(open) => open && setOpened(true)}>
      <div className="stack">{children(opened)}</div>
    </Section>
  )
}

export default function ReviewStage() {
  const { dramaId, drama, refetchDrama } = useStage()
  // Bumped after any write or finished job; every panel refetches on it.
  const [reloads, setReloads] = useState(0)
  // The header and tab counts come from the shell's workflow progress, which only
  // reloads with the drama; the filter chips come from the lines list. Refreshing
  // both here keeps the three in step after every write.
  const changed = useCallback(() => {
    setReloads((n) => n + 1)
    refetchDrama()
  }, [refetchDrama])
  const jobRunning = useDramaJobRunning(dramaId, reloads)
  // A job that finished (translation, re-transcribe) rewrote lines the list has not seen yet.
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !jobRunning) changed()
    wasRunning.current = jobRunning
  }, [jobRunning, changed])
  // Bumped by the selection bar's "Compare transcription…": opens the fold and
  // the section and shows the ticked lines there.
  const [compareSignal, setCompareSignal] = useState(0)
  const openCompare = useCallback(() => setCompareSignal((n) => n + 1), [])
  // Same for "Re-time with Qwen3 aligner…".
  const [retimeSignal, setRetimeSignal] = useState(0)
  const openRetime = useCallback(() => setRetimeSignal((n) => n + 1), [])
  // Same for "Re-transcribe selected", the waveform's "Transcribe this gap" and
  // "Add and transcribe": ids start a run on exactly those lines.
  const [retranscribeRequest, setRetranscribeRequest] = useState<RetranscribeRequest | null>(null)
  const openRetranscribe = useCallback(
    (lineIds: number[] | null) => setRetranscribeRequest((r) => ({ seq: (r?.seq ?? 0) + 1, lineIds })),
    [],
  )
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
    <LineSelectionProvider titleId={dramaId}>
    <div className="stage-review" role="region" aria-label="Review">
      <LinesPanel
        dramaId={dramaId}
        reloads={reloads}
        onChanged={changed}
        jobRunning={jobRunning}
        mediaKind={mediaKind}
        sourceLanguage={drama.source_language}
        onLineCount={setLineCount}
        onFlaggedCount={setFlaggedCount}
        onCompareSelected={openCompare}
        onRetimeSelected={openRetime}
        onRetranscribeLines={openRetranscribe}
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
              <Fold storageKey="review.fold.restructure" title="Restructure lines" summary="Structure · re-split · merge short · fix common errors · shorten overlong">
                {() => (
                  <>
                    <StructureSection dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />
                    <ResplitLines dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />
                    <AiExtrasMerge dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />
                    <AiExtrasCleanup dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />
                    {parts.shorten}
                  </>
                )}
              </Fold>
            )}
            <Fold storageKey="review.fold.history" title="Versions and history" openSignal={compareSignal + retimeSignal + (retranscribeRequest?.seq ?? 0) || undefined} summary="Notes · versions · history · compare · compare transcription · re-time · re-transcribe · edit tendencies">
              {(opened) => (
                <>
                  <RecordsPanel dramaId={dramaId} reloads={reloads} onChanged={changed} jobRunning={jobRunning} onGoTo={goToLine} />
                  {parts.history}
                  {!!lineCount && opened && <CompareTranscription dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} openSignal={compareSignal || undefined} />}
                  {!!lineCount && opened && <RetimeLines dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} openSignal={retimeSignal || undefined} />}
                  {!!lineCount && opened && <RetranscribeLines dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} request={retranscribeRequest} />}
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
    </LineSelectionProvider>
  )
}
