import { useCallback, useEffect, useRef, useState } from 'react'

import type { PlayerMediaKind } from '../../../api/media'
import { getMediaStatus } from '../../../api/workspace'
import { useRoute } from '../../../router'
import { useStage } from '../StageContext'
import { AiExtrasBurnPreview } from './review/AiExtrasBurnPreview'
import { AiExtrasCleanup } from './review/AiExtrasCleanup'
import { AiExtrasMerge } from './review/AiExtrasMerge'
import { AiExtrasSenseVoice } from './review/AiExtrasSenseVoice'
import { AiExtrasStyle } from './review/AiExtrasStyle'
import { CompareTranscription } from './review/CompareTranscription'
import { RetimeLines } from './review/RetimeLines'
import { RetranslateSelected } from './review/RetranslateSelected'
import { ThreeRunReview } from './review/ThreeRunReview'
import { RetranscribeLines, type RetranscribeRequest } from './review/RetranscribeLines'
import { LinesPanel } from './review/LinesPanel'
import { LineSelectionProvider } from './review/LineSelectionContext'
import { RecordsPanel } from './review/RecordsPanel'
import { ReviewChecks } from './review/ReviewChecks'
import { MarkEpisodeReviewed } from './review/MarkEpisodeReviewed'
import { ReviewFlags } from './review/ReviewFlags'
import { ReviewJobsPanel } from './review/ReviewJobsPanel'
import { ReviewTools } from './review/ReviewTools'
import { VoiceClonePanel } from './VoiceClonePanel'
import type { ReviewTool } from './review/reviewToolsTabs'
import { ResplitLines } from './review/ResplitLines'
import { StructureSection } from './review/StructureSection'
import type { LineTarget } from './review/reviewResults'
import { useDramaJobRunning } from './review/useDramaJobRunning'
import './review/review.css'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'

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
  // A write that splits, merges or replaces lines: ticked ids may no longer be
  // the lines they were, so the selection is dropped.
  const [reshapes, setReshapes] = useState(0)
  const reshaped = useCallback(() => {
    setReshapes((n) => n + 1)
    changed()
  }, [changed])
  const jobRunning = useDramaJobRunning(dramaId, reloads)
  // A job that finished (translation, re-transcribe) rewrote lines the list has not seen yet.
  const wasRunning = useRef(false)
  useEffect(() => {
    if (wasRunning.current && !jobRunning) changed()
    wasRunning.current = jobRunning
  }, [jobRunning, changed])
  // Those three buttons open tools in the panel below, so each also switches to the tool's group.
  const [toolsRequest, setToolsRequest] = useState<{ tool: ReviewTool; seq: number } | null>(null)
  const showTool = useCallback((tool: ReviewTool) => setToolsRequest((r) => ({ tool, seq: (r?.seq ?? 0) + 1 })), [])
  // Bumped by the selection bar's "Compare transcription…": opens the fold and
  // the section and shows the ticked lines there.
  const [compareSignal, setCompareSignal] = useState(0)
  const openCompare = useCallback(() => {
    setCompareSignal((n) => n + 1)
    showTool('compareTranscription')
  }, [showTool])
  // Same for "Re-time with Qwen3 aligner…".
  const [retimeSignal, setRetimeSignal] = useState(0)
  const openRetime = useCallback(() => {
    setRetimeSignal((n) => n + 1)
    showTool('retime')
  }, [showTool])
  // Same for "Re-transcribe selected", the waveform's "Transcribe this gap" and
  // "Add and transcribe": ids start a run on exactly those lines.
  const [retranscribeRequest, setRetranscribeRequest] = useState<RetranscribeRequest | null>(null)
  const [handledRetranscribeSeq, setHandledRetranscribeSeq] = useState(0)
  const pendingRetranscribe = retranscribeRequest && retranscribeRequest.seq !== handledRetranscribeSeq ? retranscribeRequest : null
  const openRetranscribe = useCallback(
    (lineIds: number[] | null) => {
      setRetranscribeRequest((r) => ({ seq: (r?.seq ?? 0) + 1, lineIds }))
      showTool('retranscribe')
    },
    [showTool],
  )
  // A finished comparison's link (the Jobs page's Review) names Compare transcription, which Simple leaves out of
  // the tools; asking for it like the selection bar does brings it in, so the link does not land without it.
  const route = useRoute()
  const linkedCompare = route.name === 'drama' && route.id === dramaId && route.focus === 'compare'
  useEffect(() => {
    if (linkedCompare) showTool('compareTranscription')
  }, [linkedCompare, showTool])
  const [lineCount, setLineCount] = useState<number | null>(null)
  // A finding's line link: the editor opens that line (by id where known).
  const [goTo, setGoTo] = useState<{ target: LineTarget; seq: number; resolve: (m: string | null) => void } | null>(null)
  const goToLine = useCallback(
    (target: LineTarget) =>
      new Promise<string | null>((resolve) => setGoTo((g) => ({ target, seq: (g?.seq ?? 0) + 1, resolve }))),
    [],
  )
  const [flaggedCount, setFlaggedCount] = useState<number | null>(null)
  // Not loaded yet counts as no lines: showing the panel first would flash it on a title that has none.
  // A ?tools= link or a running job still opens the panel with no lines.
  const hideTools = !lineCount && !jobRunning && !(route.name === 'drama' && route.id === dramaId && route.tools)

  // Play from the source video when there is one (its sound plays even while
  // the picture is folded away), else from the audio; no media, no player.
  const [mediaKind, setMediaKind] = useState<PlayerMediaKind | null>(null)
  useCancellableEffect((cancelled) => {
    getMediaStatus(dramaId).then(
      (s) => !cancelled() && setMediaKind(s.has_source_video ? 'video' : s.has_audio ? 'audio' : null),
      () => !cancelled() && setMediaKind(drama.has_audio ? 'audio' : null),
    )
  }, [dramaId, drama.has_audio])

  return (
    <LineSelectionProvider titleId={dramaId} reshapes={reshapes}>
    <div className="stage-review" role="region" aria-label="Review">
      <LinesPanel
        dramaId={dramaId}
        reloads={reloads}
        onChanged={changed}
        jobRunning={jobRunning}
        mediaKind={mediaKind}
        sourceLanguage={drama.source_language}
        prose={drama.content_mode === 'novel_narration'}
        translationEngine={drama.translation_engine}
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
          <ReviewJobsPanel dramaId={dramaId} reloads={reloads} onChanged={changed} onGoTo={goToLine} flaggedCount={flaggedCount}
            render={(jobs) => hideTools ? null : (
              <ReviewTools
                dramaId={dramaId}
                status={jobs.status}
                request={toolsRequest}
                attention={{ coverage: parts.coverageCount }}
                tools={{
                  checks: jobs.check,
                  flags: !!lineCount && <ReviewFlags onDone={changed} />,
                  coverage: parts.coverage,
                  compareTranscription: !!lineCount && <CompareTranscription dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} openSignal={compareSignal || undefined} />,
                  audioTags: !!lineCount && <AiExtrasSenseVoice dramaId={dramaId} reloads={reloads} />,
                  fixFlagged: jobs.fix,
                  retranslate: !!lineCount && <RetranslateSelected dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />,
                  threeRun: !!lineCount && <ThreeRunReview dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />,
                  structure: !!lineCount && <StructureSection dramaId={dramaId} jobRunning={jobRunning} onChanged={reshaped} />,
                  resplit: !!lineCount && <ResplitLines dramaId={dramaId} jobRunning={jobRunning} onChanged={reshaped} />,
                  merge: !!lineCount && <AiExtrasMerge dramaId={dramaId} jobRunning={jobRunning} onChanged={reshaped} />,
                  shorten: !!lineCount && parts.shorten,
                  cleanup: !!lineCount && <AiExtrasCleanup dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />,
                  retime: !!lineCount && <RetimeLines dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} openSignal={retimeSignal || undefined} />,
                  retranscribe: !!lineCount && <RetranscribeLines dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} request={pendingRetranscribe} onRequestHandled={setHandledRetranscribeSeq} />,
                  voices: <VoiceClonePanel />,
                  records: <RecordsPanel dramaId={dramaId} reloads={reloads} onChanged={changed} onReshaped={reshaped} jobRunning={jobRunning} onGoTo={goToLine} />,
                  versionsAndNotes: parts.history,
                  style: !!lineCount && <AiExtrasStyle dramaId={dramaId} reloads={reloads} />,
                  burnPreview: !!lineCount && <AiExtrasBurnPreview dramaId={dramaId} />,
                  markReviewed: !!lineCount && <MarkEpisodeReviewed dramaId={dramaId} jobRunning={jobRunning} onChanged={changed} />,
                }}
              />
            )}
          />
        )}
      />
    </div>
    </LineSelectionProvider>
  )
}
