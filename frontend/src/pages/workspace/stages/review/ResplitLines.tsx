import { useCallback, useEffect, useRef, useState } from 'react'

import { getInstallPresets, installDependency } from '../../../../api/diagnostics'
import { getSettings } from '../../../../api/settings'
import { getDiarizationConfig } from '../../../../api/workspace'
import { ApiError } from '../../../../api/client'
import { listAllLines, reassignSpeakersFromSaved, resplitLines, restoreSnapshot } from '../../../../api/restructure'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { ConfirmButton } from '../../../../components/ConfirmButton'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { Toggle } from '../../../../components/Toggle'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { usePcOnly } from '../../../../hooks/usePcOnly'
import { useReattachJob } from '../../../../hooks/useReattachJob'
import { useStageDraft } from '../../../../hooks/useStageDraft'
import { jobSucceeded, type JobRecord } from '../../../../types/jobs'
import type { ResplitResult, ResplitSensitivity } from '../../../../types/restructure'
import { runInstallJob } from '../../../diagnostics/installJob'
import { useInstallPlan } from '../../../diagnostics/useInstallPlan'
import { useStage } from '../../StageContext'
import { resplitJobId } from '../../stageJobIds'
import { JobPanel } from '../JobPanel'
import type { SpeakerTimeSummary } from '../../../../types/workspace'
import { speakerTimeFooter, speakerTimeLines, structureErrorText, undoDoneMessage, undoHandleOf, undoRefusal, type UndoHandle } from './reviewLogic'
import { JOB_RUNNING_MESSAGE, QUICK_SPLIT_DEFAULTS, RESPLIT_DRAFT_SHAPE, RESPLIT_DRAFT_STAGE, RESPLIT_DURATION_CAPS, RESPLIT_SENSITIVITIES, quickSplitHelp, quickSplitLimits, resplitNeedsConfirm, resplitPreviewSummary, resplitSummary, wordSplitterPackages } from './reviewResegment'
import { UndoNotice } from './UndoNotice'
import { retireUndoOffer, useUndoOffer } from './undoOffer'

// Lines over the quick-split limits (Settings > Timing and splitting; 12 s and 40
// characters unless changed), cut at sentence ends, then commas, then pauses, then evenly,
// and timed from the audio when the aligner is available.

interface Props {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
}

// Cuts over-long lines in place from the text already saved, and relabels
// only the split lines from the detection already saved: nothing is
// transcribed or detected again. A snapshot is taken first (Records -> Line history).
export function ResplitLines({ dramaId, jobRunning, onChanged }: Props) {
  const { onJobDone, drama } = useStage()
  const pc = usePcOnly()
  // The options as last left here; a cap of 0 means the preset's own limit.
  const { draft, save: saveDraft } = useStageDraft(dramaId, RESPLIT_DRAFT_STAGE, RESPLIT_DRAFT_SHAPE)
  const [align, setAlign] = useState(draft.align ?? false)
  const [sensitivity, setSensitivity] = useState<ResplitSensitivity>(
    RESPLIT_SENSITIVITIES.some((o) => o.value === draft.sensitivity) ? (draft.sensitivity as ResplitSensitivity) : 'more',
  )
  const [cap, setCap] = useState<number | null>(RESPLIT_DURATION_CAPS.includes(draft.capSec ?? 0) ? (draft.capSec as number) : null)
  // "More" cuts by length, which splits Chinese and Japanese words in half
  // unless the word splitter is installed; an unreadable check leaves it on
  // (the server refuses it anyway).
  const [missingSplitters, setMissingSplitters] = useState<string[]>([])
  const [splitterTick, setSplitterTick] = useState(0)
  const splitterPackages = wordSplitterPackages(drama.source_language)
  useEffect(() => {
    if (!splitterPackages.length) return
    void getInstallPresets().then(
      (p) => setMissingSplitters(splitterPackages.filter((name) => !p.packages[name]?.installed)),
      () => {},
    )
  }, [splitterPackages.join(), splitterTick]) // eslint-disable-line react-hooks/exhaustive-deps
  const moreBlocked = splitterPackages.length > 0 && missingSplitters.length > 0
  const chosen: ResplitSensitivity = moreBlocked && sensitivity === 'more' ? 'sentence' : sensitivity
  useEffect(() => {
    saveDraft({ align, sensitivity, capSec: cap ?? 0 })
  }, [saveDraft, align, sensitivity, cap])
  // The saved one-click limits; another device or a member who cannot read Settings keeps the defaults.
  const [quickLimits, setQuickLimits] = useState(QUICK_SPLIT_DEFAULTS)
  useEffect(() => {
    void getSettings().then((s) => setQuickLimits(quickSplitLimits(s.preferences)), () => {})
  }, [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [needsConfirm, setNeedsConfirm] = useState(false)
  const [summary, setSummary] = useState<string | null>(null)
  const [undo, setUndo] = useUndoOffer<UndoHandle>('resplit', dramaId)
  const undoing = useRef(false)
  // Set when the Undo notice (and the button that had focus) goes away, so the
  // status line that replaces it takes focus instead of the page body.
  const focusStatus = useRef(false)
  const takeFocus = (el: HTMLParagraphElement | null) => {
    if (el && focusStatus.current) {
      focusStatus.current = false
      el.focus()
    }
  }
  // A job finished after a reload is reattached, but its undo is not offered then.
  const startedHere = useRef(false)
  const [speakers, setSpeakers] = useState<SpeakerTimeSummary | null>(null)
  const loadSpeakers = useCallback(
    () => getDiarizationConfig(dramaId).then((c) => setSpeakers(c.speaker_summary ?? null), () => {}),
    [dramaId],
  )
  useEffect(() => {
    void loadSpeakers()
  }, [loadSpeakers])
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  useReattachJob([resplitJobId(dramaId)], adoptJob)
  const { job, done, error: pollError } = useJob(jobId, { runKey, onDone: (j) => jobDone(j) })
  const running = jobId !== null && !done && !pollError
  const blocked = jobRunning || running || busy
  const wait = (jobRunning || running) ? JOB_RUNNING_MESSAGE : null

  function jobDone(j: JobRecord) {
    const r = (j.result ?? {}) as ResplitResult & { failed_reason?: string; detail?: string }
    if (jobSucceeded(j) && !r.failed_reason) {
      setSummary(resplitSummary(r))
      const handle = startedHere.current ? undoHandleOf(r) : null
      if (handle) setUndo(handle)
      else if (r.split_lines) retireUndoOffer()
    }
    else if (r.failed_reason === 'not_applied') setSummary(`Nothing was changed. ${r.detail ?? ''}`.trim())
    onJobDone()
    onChanged()
    void loadSpeakers()
  }

  // The one-click "Split long lines" run, remembered so "Split anyway" repeats it.
  const quick = useRef(false)

  const run = async (confirm: boolean, dryRun = false, oneClick = quick.current) => {
    quick.current = oneClick
    setBusy(true)
    setError(null)
    setSummary(null)
    setUndo(null)
    setNeedsConfirm(false)
    try {
      const lines = await listAllLines(dramaId)
      const r = await resplitLines(dramaId, {
        expected_line_ids: lines.map((l) => l.id), confirm, dry_run: dryRun,
        ...(oneClick ? { align_to_audio: true, max_seconds: quickLimits.seconds } : { align_to_audio: align, sensitivity: chosen, max_seconds: cap }),
      })
      if (dryRun) setSummary(resplitPreviewSummary(r))
      else if (r.job_id) {
        startedHere.current = true
        setJobId(r.job_id)
      } else {
        setSummary(resplitSummary(r))
        const handle = undoHandleOf(r)
        if (handle) setUndo(handle)
        else if (r.split_lines) retireUndoOffer()
        if (r.split_lines) onChanged()
        void loadSpeakers()
      }
    } catch (e) {
      if (resplitNeedsConfirm(e)) setNeedsConfirm(true)
      else setError(e)
    } finally {
      setBusy(false)
    }
  }

  const doUndo = async () => {
    // A ref, not state: two quick clicks must send one restore.
    if (!undo || undoing.current) return
    undoing.current = true
    setBusy(true)
    setError(null)
    try {
      const lines = await listAllLines(dramaId)
      await restoreSnapshot(dramaId, undo.historyId, lines.map((l) => l.id), undo.fingerprint)
      focusStatus.current = true
      setUndo(null)
      setSummary(undoDoneMessage('resplit'))
      onChanged()
      void loadSpeakers()
    } catch (e) {
      const refused = undoRefusal(e)
      if (refused) {
        if (!refused.keepOffer) {
          focusStatus.current = true
          setUndo(null)
        }
        setSummary(refused.text)
      } else setError(e)
    } finally {
      undoing.current = false
      setBusy(false)
    }
  }

  // The install goes through the preview first: a risky or replacing plan needs its panel, not a raw 409.
  const { planned, withPlan, view: planView } = useInstallPlan({
    onChecking: () => {
      setError(null)
      setBusy(true)
    },
    onChecked: () => setBusy(false),
    onQueued: (label) => {
      setBusy(false)
      setSummary(`${label} is queued. It installs when you restart Baihe.`)
    },
    onQueueFailed: (_label, e) => {
      setBusy(false)
      setError(e)
    },
  })
  const installSplitters = () =>
    withPlan(missingSplitters.join(' and '), missingSplitters, async (acceptedRisks) => {
      setBusy(true)
      setError(null)
      try {
        for (const name of missingSplitters) {
          const r = await runInstallJob(() => installDependency(name, undefined, acceptedRisks), undefined, name)
          if (!r.ok) {
            setSummary(r.hint || `Couldn't install ${name}.`)
            break
          }
        }
        setSplitterTick((t) => t + 1)
      } catch (e) {
        setError(e)
      } finally {
        setBusy(false)
      }
    })

  const reassign = async () => {
    setBusy(true)
    setError(null)
    setSummary(null)
    setUndo(null)
    try {
      const r = await reassignSpeakersFromSaved(dramaId)
      setSummary(`Speakers re-assigned from the saved detection: ${r.changed} changed${r.kept_manual ? `, ${r.kept_manual} kept as you set them` : ''}.`)
      if (r.changed) onChanged()
      void loadSpeakers()
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && !/job/i.test(e.message)) setSummary(e.message)
      else setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div role="group" aria-label="Re-split long lines">
      <Section storageKey="review.resplit" title="Re-split long lines" summary="Cut long blocks · re-assign speakers">
        <p className="muted">
          Cuts over-long lines at sentence ends (then commas) using the text you already have. Lines transcribed since word timings
          are kept can also be cut at real pauses between words, with real times; older lines keep the estimate. Only the split lines get speakers from
          the saved detection; other lines keep theirs. Nothing is transcribed or detected again.
        </p>
        <div className="setting-list review-toggles">
          <Field
            label="Split sensitivity"
            help="More: lines over the usual subtitle length for their language (16 Chinese/Japanese, 20 Korean, 42 English characters), cut at word boundaries. Sentence by sentence: every sentence end. Lines over 8 s or 40 CJK characters are what a fresh transcription already splits; Split long lines handles those. Pieces under 0.8 s or 4 CJK characters / 2 words are never made."
          >
            <select value={chosen} disabled={blocked} aria-describedby={moreBlocked ? 'resplit-more-blocked' : undefined}
              onChange={(e) => { setSensitivity(e.target.value as ResplitSensitivity); setSummary(null) }}>
              {RESPLIT_SENSITIVITIES.map((o) => (
                <option key={o.value} value={o.value} disabled={o.value === 'more' && moreBlocked}>{o.label}</option>
              ))}
            </select>
          </Field>
          {moreBlocked && (
            <div id="resplit-more-blocked" className="muted" data-testid="resplit-more-blocked">
              <p>"More" is off: it needs the {missingSplitters.join(' and ')} word splitter, which isn't installed.</p>
              {pc === 'remote' ? <p>Ask the PC owner to install it in Diagnostics.</p> : (
                <ConfirmButton name={missingSplitters.join(' and ')} verb="install" tone="primary" label="Install…"
                  confirmLabel={`Confirm install ${missingSplitters.join(' and ')}`} busy={busy} disabled={blocked || !!planned} onConfirm={() => void installSplitters()} />
              )}
              {planView}
            </div>
          )}
          <Field
            label="Also split by duration"
            unit="s"
            help="Lines longer than this are split even if short in characters. It replaces the preset's own duration limit (8 s for Normal and More, none for Sentence by sentence)."
          >
            <select value={cap ?? ''} disabled={blocked} onChange={(e) => { setCap(e.target.value ? Number(e.target.value) : null); setSummary(null) }}>
              <option value="">Preset default</option>
              {RESPLIT_DURATION_CAPS.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </Field>
          <Field
            label="Align to audio"
            help="Times the pieces from the audio with the Qwen3 forced aligner (a background job that can use the GPU). Off, the cuts are estimated from text length. If the aligner or audio is missing, estimated timing is used and you are told."
          >
            <Toggle checked={align} onChange={setAlign} />
          </Field>
        </div>
        <div className="actions">
          <button type="button" className="primary" disabled={blocked} onClick={() => run(false, false, true)}
            title={quickSplitHelp(quickLimits)}>
            Split long lines
          </button>
          <button type="button" disabled={blocked} onClick={() => run(false, true, false)}>
            Preview split
          </button>
          <button type="button" disabled={blocked} onClick={() => run(false, false, false)}>
            {busy || running ? 'Splitting…' : 'Re-split long lines'}
          </button>
          <button type="button" disabled={blocked} onClick={reassign}>
            Re-assign speakers from saved detection
          </button>
        </div>
        {speakers && (
          <div data-testid="speaker-time" className="resplit-speakers">
            <ul aria-label="Speaking time per speaker" className="muted">
              {speakerTimeLines(speakers).map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
            <p className="muted">{speakerTimeFooter(speakers)}</p>
          </div>
        )}
        {wait && !busy && <p className="muted" data-testid="resplit-wait">{wait}</p>}
        {needsConfirm && (
          <div className="reseg-ai-warn" role="alert" data-testid="resplit-confirm">
            <p>Some long lines already have English. Their pieces can't share it, so splitting clears the English on those lines only.</p>
            <button type="button" disabled={blocked} onClick={() => run(true)}>Split anyway</button>
          </div>
        )}
        {summary && undo && <UndoNotice message={summary} busy={blocked} onUndo={() => void doUndo()} onDismiss={() => setUndo(null)} testId="resplit-summary" />}
        {summary && !undo && (
          <p role="status" data-testid="resplit-summary" tabIndex={-1} ref={takeFocus}>
            {summary}
          </p>
        )}
        {structureErrorText(error) ? (
          <p className="error" role="alert">{structureErrorText(error)}</p>
        ) : (
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        )}
      </Section>
      {/* No Retry: a re-split needs its own confirm step. */}
      <JobPanel jobId={jobId} job={job} pollError={pollError} lastRun={{ dramaId, ids: [resplitJobId(dramaId)] }} />
    </div>
  )
}
