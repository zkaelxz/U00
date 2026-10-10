import { useEffect, useMemo, useRef, useState } from 'react'

import { cancelJob } from '../../../../api/jobs'
import {
  applyCompare,
  estimateCompare,
  getCompareOptions,
  getCompareResult,
  startCompare,
} from '../../../../api/workspace'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { Toggle } from '../../../../components/Toggle'
import { buttonClass } from '../../../../components/uiClasses'
import { useJob, useJobRun } from '../../../../hooks/useJob'
import { useStageDraft } from '../../../../hooks/useStageDraft'
import { useLineSelectionContext } from './LineSelectionContext'
import { transcribeExtraNames } from '../../sourceForm'
import { promptFields } from '../transcribePrompt'
import { TERMINAL_STATUSES } from '../../../../types/jobs'
import type { CompareEstimate, CompareOptions, CompareProposal, CompareResult } from '../../../../types/workspace'
import {
  COMPARE_DRAFT_SHAPE,
  COMPARE_DRAFT_STAGE,
  EMPTY_SELECTION_FORM,
  buildSelection,
  capProblem,
  compareOutcome,
  PROMPT_MAX_CHARS,
  isSameText,
  promptProblem,
  textDiff,
  type SelectionForm,
  type SelectionMode,
} from './compareTranscriptionLogic'

const MODES: { id: SelectionMode; label: string }[] = [
  // Its label carries the live count; see the picker.
  { id: 'selected', label: 'Selected lines' },
  { id: 'flagged', label: 'All flagged lines' },
  { id: 'line', label: 'One line' },
  { id: 'range', label: 'A line range' },
  { id: 'speaker', label: 'All lines by one speaker' },
  { id: 'time', label: 'All lines in a time range' },
]

function Marked({ text, parts }: { text: string; parts: { text: string; changed: boolean }[] }) {
  if (!text) return <span className="muted">(empty)</span>
  return (
    <>
      {parts.map((p, i) => (p.changed ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
    </>
  )
}

function Cell({ a, b, lang }: { a: string; b: string; lang?: string }) {
  const d = useMemo(() => textDiff(a, b), [a, b])
  return (
    <>
      <td lang={lang} data-label="Current"><Marked text={a} parts={d.a} /></td>
      <td lang={lang} data-label="Candidate"><Marked text={b} parts={d.b} /></td>
    </>
  )
}

// Re-hears chosen lines with a model or backend other than the title's saved
// one and lays the candidates beside the current text (and, optionally, their
// English), so a reader who can't judge the source can compare the English.
// Nothing is written until "Use this" / "Use all shown"; the server then
// writes only source text (and English where asked) for lines unchanged since
// the run. The lines come from a picker or from the lines ticked in Review; the alignment method is shown but fixed (it sets timing, not text).
export function CompareTranscription({
  dramaId,
  jobRunning,
  onChanged,
  openSignal,
}: {
  dramaId: number
  jobRunning: boolean
  onChanged: () => void
  // Each change (the selection bar's action) opens the section, scrolls to it
  // and switches the picker to the ticked lines.
  openSignal?: number
}) {
  const { selectedIds } = useLineSelectionContext()
  const panelRef = useRef<HTMLDivElement>(null)
  // Counted after mount so a section mounted by the very action that opens it still sees a change.
  const [sectionSignal, setSectionSignal] = useState(0)
  const [options, setOptions] = useState<CompareOptions | null>(null)
  const [form, setForm] = useState<SelectionForm>(EMPTY_SELECTION_FORM)
  // The run options as last left here; the names start from the Transcribe stage's.
  const { draft, save: saveDraft, clear: clearDraft } = useStageDraft(dramaId, COMPARE_DRAFT_STAGE, COMPARE_DRAFT_SHAPE)
  const [size, setSize] = useState('')
  const [backend, setBackend] = useState('')
  const [translate, setTranslate] = useState(draft.translate ?? false)
  const [retranslate, setRetranslate] = useState(draft.retranslate ?? false)
  const [extraNames, setExtraNames] = useState(() => draft.extraNames ?? transcribeExtraNames(dramaId))
  const [hint, setHint] = useState(draft.hint ?? '')
  useEffect(() => {
    if (options) saveDraft({ size, backend, translate, retranslate, extraNames, hint })
  }, [saveDraft, options, size, backend, translate, retranslate, extraNames, hint])
  const [usedPrompt, setUsedPrompt] = useState<{ hint: string; names: string } | null>(null)
  const [estimate, setEstimate] = useState<CompareEstimate | null>(null)
  const [estimateError, setEstimateError] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [starting, setStarting] = useState(false)
  const [applying, setApplying] = useState(false)
  const [jobId, setJobId, runKey] = useJobRun()
  const [result, setResult] = useState<CompareResult | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  const [english, setEnglish] = useState<Record<number, boolean>>({})
  const [note, setNote] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getCompareOptions(dramaId).then(
      (o) => {
        if (cancelled) return
        setOptions(o)
        setSize(draft.size && o.whisper_sizes.includes(draft.size) ? draft.size : o.saved_whisper_size)
        setBackend(draft.backend && o.backends.some((b) => b.id === draft.backend) ? draft.backend : o.saved_asr_backend)
      },
      (e) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
    // draft is read once per drama with it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dramaId])

  useEffect(() => {
    if (!openSignal) return
    setForm((f) => ({ ...f, mode: 'selected' }))
    setSectionSignal((n) => n + 1)
    panelRef.current?.scrollIntoView({ block: 'start' })
  }, [openSignal])

  const built = buildSelection(form, selectedIds, options?.max_lines)
  const selectionKey = 'selection' in built ? JSON.stringify(built.selection) : null

  // The advisory count and cost for the current selection, before anything starts.
  useEffect(() => {
    setEstimate(null)
    setEstimateError(null)
    if (!('selection' in built) || !options?.has_audio) return
    let cancelled = false
    const t = setTimeout(() => {
      estimateCompare(dramaId, { selection: built.selection, translate, retranslate_current: retranslate }).then(
        (e) => !cancelled && setEstimate(e),
        (e) => !cancelled && setEstimateError(e instanceof Error ? e.message : 'Could not check this selection.'),
      )
    }, 300)
    return () => {
      cancelled = true
      clearTimeout(t)
    }
    // built is derived from form; selectionKey is its stable identity.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dramaId, selectionKey, translate, retranslate, options?.has_audio])

  const { job, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      const o = compareOutcome(j)
      if (o.kind === 'none') {
        setFailure(o.text)
        return
      }
      getCompareResult(dramaId).then((r) => {
        setResult(r)
        setEnglish({})
      }, setError)
    },
  })

  if (!options) {
    return (
      <div ref={panelRef}>
        <Section storageKey="review.compareTranscription" title="Compare transcription" summary="Loading…" openSignal={sectionSignal}>
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </Section>
      </div>
    )
  }

  const active = !!job && !TERMINAL_STATUSES.includes(job.status)
  const busy = starting || active || (!!jobId && !job && !pollError)
  const tooMany = estimate ? capProblem(estimate.line_count, options.max_lines) : null
  const blockedMessage = !options.has_audio
    ? options.no_audio_reason
    : jobRunning && !busy
      ? 'Another job is running on this title. Try again when it finishes.'
      : 'problem' in built
        ? built.problem
        : promptProblem(hint, extraNames)
          ? promptProblem(hint, extraNames)
        : tooMany ?? estimateError ?? (estimate?.monthly_refusal ? 'This month’s spending cap is used up, so translation can’t run.' : null)
  const chosenBackend = options.backends.find((b) => b.id === backend)

  const start = () => {
    if (!('selection' in built)) return
    setError(null)
    setResult(null)
    setFailure(null)
    setNote(null)
    setStarting(true)
    startCompare(dramaId, {
      selection: built.selection,
      whisper_size: size,
      asr_backend: backend,
      translate,
      retranslate_current: translate && retranslate,
      ...promptFields(hint, extraNames),
    })
      .then((r) => {
        setUsedPrompt({ hint: hint.trim(), names: hint.trim() ? '' : extraNames.trim() })
        setJobId(r.job_id)
      }, setError)
      .finally(() => setStarting(false))
  }

  const rows = (result?.proposals ?? []).filter((p) => !isSameText(p.base_zh, p.candidate_zh))
  const same = (result?.proposals.length ?? 0) - rows.length

  const apply = (chosen: CompareProposal[]) => {
    if (!result || chosen.length === 0) return
    setError(null)
    setApplying(true)
    applyCompare(dramaId, {
      job_id: result.job_id,
      items: chosen.map((p) => {
        const withEnglish = !!english[p.line_id] && !!p.candidate_en
        return {
          line_id: p.line_id,
          expected_base_zh: p.base_zh,
          expected_candidate_zh: p.candidate_zh,
          use_english: withEnglish,
          expected_candidate_en: withEnglish ? p.candidate_en : '',
        }
      }),
    })
      .then((r) => {
        const done = new Set(r.applied)
        setResult((cur) => (cur ? { ...cur, proposals: cur.proposals.filter((p) => !done.has(p.line_id)) } : cur))
        setNote(
          `Replaced ${r.applied.length} line${r.applied.length === 1 ? '' : 's'}.` +
            (r.skipped.length ? ` ${r.skipped.length} edited since the comparison, so left alone.` : ''),
        )
        onChanged()
      }, setError)
      .finally(() => setApplying(false))
  }

  const setMode = (mode: SelectionMode) => setForm((f) => ({ ...f, mode }))
  const num = (key: keyof SelectionForm, label: string, max?: number) => (
    <Field label={label}>
      <input
        type="number"
        inputMode="numeric"
        min={0}
        max={max}
        value={form[key]}
        onChange={(e) => setForm((f) => ({ ...f, [key]: e.target.value }))}
        disabled={busy}
      />
    </Field>
  )

  return (
    <div ref={panelRef}>
    <Section
      storageKey="review.compareTranscription"
      title="Compare transcription"
      summary={`Saved: ${options.saved_asr_backend} · ${options.saved_whisper_size}`}
      openSignal={sectionSignal}
    >
      <div className="compare-panel" data-testid="compare-transcription">
        <p className="muted">
          Hears the chosen lines again with a model or backend of your choice and shows it beside what is there now.
          Nothing changes until you press “Use this”.
        </p>
        <Field label="Which lines">
          <select value={form.mode} onChange={(e) => setMode(e.target.value as SelectionMode)} disabled={busy}>
            {MODES.filter((m) => m.id !== 'selected' || selectedIds.length > 0 || form.mode === 'selected').map((m) => (
              <option key={m.id} value={m.id}>{m.id === 'selected' ? `${m.label} (${selectedIds.length})` : m.label}</option>
            ))}
          </select>
        </Field>
        {form.mode === 'line' && num('lineNumber', 'Line number')}
        {form.mode === 'range' && (
          <div className="compare-pair">
            {num('from', 'From line #')}
            {num('to', 'To line #')}
          </div>
        )}
        {form.mode === 'speaker' && (
          <Field label="Speaker">
            <input type="text" value={form.speaker} onChange={(e) => setForm((f) => ({ ...f, speaker: e.target.value }))} disabled={busy} />
          </Field>
        )}
        {form.mode === 'time' && (
          <div className="compare-pair">
            {num('startSeconds', 'From (seconds)')}
            {num('endSeconds', 'To (seconds)')}
          </div>
        )}
        <div className="compare-pair">
          <Field label="Whisper model">
            <select value={size} onChange={(e) => setSize(e.target.value)} disabled={busy}>
              {options.whisper_sizes.map((s) => (
                <option key={s} value={s}>{s}{s === options.saved_whisper_size ? ' (saved)' : ''}</option>
              ))}
            </select>
          </Field>
          <Field label="ASR backend">
            <select value={backend} onChange={(e) => setBackend(e.target.value)} disabled={busy}>
              {options.backends.map((b) => (
                <option key={b.id} value={b.id} disabled={!b.available && b.id !== backend}>
                  {b.label}{b.id === options.saved_asr_backend ? ' (saved)' : ''}{b.available ? '' : ' (unavailable)'}
                </option>
              ))}
            </select>
          </Field>
        </div>
        {chosenBackend && !chosenBackend.available && (
          <p className="error" role="alert">{chosenBackend.reason}</p>
        )}
        <Field label="Hint for the model (names, terms)" help="Optional. Replaces the automatic Whisper prompt for this run; leave empty to use the glossary names plus the extra names below.">
          <input
            value={hint}
            maxLength={PROMPT_MAX_CHARS}
            placeholder="沈清疑、云隐宗"
            onChange={(e) => setHint(e.target.value)}
            disabled={busy}
          />
        </Field>
        <Field label="Extra character names" help="Added to the automatic prompt. Separate names with 、 or commas. Ignored when a hint is given.">
          <input
            value={extraNames}
            maxLength={PROMPT_MAX_CHARS}
            placeholder="沈清疑、云隐宗"
            onChange={(e) => setExtraNames(e.target.value)}
            disabled={busy}
          />
        </Field>
        <p className="muted">
          Alignment stays “{options.saved_alignment_method}”: it decides timing, not the words heard, so it can’t change
          a candidate. One candidate per run.
        </p>
        <Field label="Also translate" help={`Translates the heard text with this title’s translation settings (${options.translation_engine}) so you can compare the English. It counts toward the monthly and job spending caps.`}>
          <Toggle checked={translate} onChange={setTranslate} disabled={busy} />
        </Field>
        {translate && (
          <Field label="Retranslate current text" help="Lines that already have English keep it unless this is on.">
            <Toggle checked={retranslate} onChange={setRetranslate} disabled={busy} />
          </Field>
        )}
        {estimate && !blockedMessage && (
          <p data-testid="compare-estimate">
            {estimate.line_count} line{estimate.line_count === 1 ? '' : 's'}
            {translate && estimate.estimated_usd !== null &&
              (estimate.free ? ' · translation is free for this engine' : ` · translation about $${estimate.estimated_usd.toFixed(2)}${estimate.effective_cap_usd !== null ? ` (cap $${estimate.effective_cap_usd.toFixed(2)})` : ''}`)}
            {estimate.estimate_above_cap && ' · above the spending cap: it will stop early'}
          </p>
        )}
        <div className="review-actions">
          <button type="button" onClick={start} disabled={busy || applying || !!blockedMessage || !chosenBackend?.available}>
            {busy ? 'Comparing…' : 'Compare'}
          </button>
          {active && job && (
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => cancelJob(job.job_id).catch(setError)}>Cancel</button>
          )}
          <button
            type="button"
            className={buttonClass('ghost')}
            disabled={busy}
            onClick={() => {
              clearDraft()
              setSize(options.saved_whisper_size)
              setBackend(options.saved_asr_backend)
              setTranslate(false)
              setRetranslate(false)
              setHint('')
              setExtraNames(transcribeExtraNames(dramaId))
            }}
          >
            Reset to defaults
          </button>
        </div>
        {blockedMessage && !busy && <p className="muted" data-testid="compare-blocked">{blockedMessage}</p>}
        <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
        {active && job && (
          <p className="muted" data-testid="compare-progress">
            {job.progress !== null && <progress value={job.progress} max={1} aria-label="Compare progress" />}{' '}
            {job.status === 'queued' ? 'Waiting for the GPU…' : job.message || 'Working…'}
          </p>
        )}
        {failure && <p className="error" role="alert" data-testid="compare-failure">{failure}</p>}
        {note && <p className="muted" role="status" data-testid="compare-note">{note}</p>}
        {result && (
          <div data-testid="compare-results">
            {usedPrompt && (usedPrompt.hint || usedPrompt.names) && (
              <p className="muted" data-testid="compare-prompt-used">
                {usedPrompt.hint ? `Hint used: ${usedPrompt.hint}` : `Extra names used: ${usedPrompt.names}`}
              </p>
            )}
            {result.partial && (
              <p className="muted" role="status">
                {result.cap_reached
                  ? 'Translation stopped at the spending cap; later lines have no English.'
                  : 'Stopped early. These are the lines heard so far.'}
              </p>
            )}
            {result.errors.length > 0 && <p className="muted">Skipped: {result.errors.join('; ')}</p>}
            {same > 0 && <p className="muted">{same} line{same === 1 ? '' : 's'} heard the same as now.</p>}
            {rows.length > 0 && (
              <>
                <div className="review-actions">
                  <button type="button" onClick={() => apply(rows)} disabled={applying}>Use all shown</button>
                  {result.translated && (
                    <label className="compare-all-english">
                      <input
                        type="checkbox"
                        checked={rows.every((p) => english[p.line_id] || !p.candidate_en)}
                        onChange={(e) =>
                          setEnglish(Object.fromEntries(rows.map((p) => [p.line_id, e.target.checked && !!p.candidate_en])))
                        }
                      />{' '}
                      Use English for all
                    </label>
                  )}
                </div>
                <div className="table-scroll">
                  <table className="compare-table" data-testid="compare-table">
                    <thead>
                      <tr>
                        <th scope="col">#</th>
                        <th scope="col">Current source</th>
                        <th scope="col">Candidate source</th>
                        {result.translated && <th scope="col">Current English</th>}
                        {result.translated && <th scope="col">Candidate English</th>}
                        <th scope="col">Use</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((p) => (
                        <tr key={p.line_id} data-testid="compare-row">
                          <td data-label="Line">{p.number}</td>
                          <Cell a={p.base_zh} b={p.candidate_zh} lang="zh" />
                          {result.translated && <Cell a={p.current_en} b={p.candidate_en} lang="en" />}
                          <td className="compare-use">
                            {p.candidate_en && (
                              <label>
                                <input
                                  type="checkbox"
                                  checked={!!english[p.line_id]}
                                  onChange={(e) => setEnglish((m) => ({ ...m, [p.line_id]: e.target.checked }))}
                                />{' '}
                                Use English
                              </label>
                            )}
                            <button type="button" onClick={() => apply([p])} disabled={applying}>Use this</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </Section>
    </div>
  )
}
