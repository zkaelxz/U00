import { useEffect, useRef, useState, type ReactNode } from 'react'

import { asrBackendOptions } from '../../../api/asrOptions'
import { analyzeMedia } from '../../../api/metadata'
import { getSettings } from '../../../api/settings'
import {
  getDiarizationConfig,
  getTranscribeConfig,
  startDiarization,
  startTranscribe,
  updateTranscribeConfig,
  uploadAndTranscribe,
} from '../../../api/workspace'
import { ButtonLink } from '../../../components/Button'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanizeValue } from '../../../components/labels'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { useMossExperimental } from '../../../hooks/useMossExperimental'
import type {
  DiarizationConfig,
  MediaStatus,
  TranscribeConfig,
  TranscribeConfigUpdate,
  TranscribeRunRequest,
} from '../../../types/workspace'
import {
  advancedSummary,
  loadSourceForm,
  parseExpectedSpeakers,
  parseSpeakerHints,
  runOptionProblem,
  runProblemFromError,
  saveSourceForm,
  validateConfig,
  type RunField,
  type RunFieldProblem,
  whisperModelWarning,
} from '../sourceForm'
import { useStage } from '../StageContext'
import { AutoTune } from './AutoTune'
import { DiarizationDeviceNote } from './DiarizationDeviceNote'
import { NovelFilePanel } from './NovelFilePanel'
import { TranscriptModePicker } from './SourceModes'
import { mediaFileInputId } from './stageBlockers'
import { diarizeEstimate, transcribeEstimate } from './transcribeEstimate'
import { promptFields } from './transcribePrompt'
import './source.css'

const WHISPER_SIZES = ['tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo']
const LANGUAGE_NAMES: Record<string, string> = { zh: 'Chinese', ja: 'Japanese', ko: 'Korean' }
// Display names for the Advanced backend choices (the option value stays raw).
const OPTION_LABELS: Record<string, string> = {
  whisper_diff: 'Whisper (diff)',
  qwen3_forced_align: 'Qwen3 forced alignment',
  whisper: 'Whisper',
  qwen3_asr: 'Qwen3 ASR',
  moss_td: 'MOSS-Transcribe-Diarize (experimental)',
  auto: 'Automatic',
  audio_separator: 'Audio Separator',
  demucs: 'Demucs',
  tesseract: 'Tesseract',
  paddle: 'PaddleOCR',
}
const optionLabel = (o: string) => OPTION_LABELS[o] ?? humanizeValue(o)

// advancedSummary names changed backends by their raw value; show their labels.
const readableSummary = (summary: string) =>
  summary
    .split(' · ')
    .map((p) => (p.startsWith('separation ') ? `separation ${optionLabel(p.slice(11))}` : (OPTION_LABELS[p] ?? p)))
    .join(' · ')

// "3 expected", "2-4", or "auto": the Speakers section's one-line summary.
function speakersSummary(expected: string, min: string, max: string): string {
  if (expected.trim()) return `${expected.trim()} expected`
  if (min.trim() || max.trim()) return `${min.trim() || '?'}-${max.trim() || '?'}`
  return 'auto'
}

interface Props {
  // The media picker (status, file input, Upload), rendered at the top of the panel.
  mediaSlot: ReactNode
  media: MediaStatus | null
  // A pre-checked file chosen in the media picker, or null.
  file: File | null
  busy: boolean
  onJobStarted: (jobId: string) => void
}

type ConfigForm = {
  whisper_size: string
  alignment_method: string
  asr_backend_choice: string
  separation_backend: string
  hardsub_ocr_backend: string
  beam_size: string
  min_silence_ms: string
  vad_threshold: string
  hardsub_interval_sec: string
  separate_vocals_first: boolean
  realign_long_segments: boolean
  whisper_fast_mode: boolean
  use_groq: boolean
}

const formFromConfig = (c: TranscribeConfig): ConfigForm => ({
  whisper_size: c.whisper_size,
  alignment_method: c.alignment_method,
  asr_backend_choice: c.asr_backend_choice,
  separation_backend: c.separation_backend,
  hardsub_ocr_backend: c.hardsub_ocr_backend,
  beam_size: String(c.beam_size),
  min_silence_ms: String(c.min_silence_ms),
  vad_threshold: String(c.vad_threshold),
  hardsub_interval_sec: String(c.hardsub_interval_sec),
  separate_vocals_first: c.separate_vocals_first,
  realign_long_segments: c.realign_long_segments,
  whisper_fast_mode: c.whisper_fast_mode,
  use_groq: c.use_groq,
})

const toUpdate = (f: ConfigForm): TranscribeConfigUpdate => ({
  ...f,
  beam_size: Number(f.beam_size),
  min_silence_ms: Number(f.min_silence_ms),
  vad_threshold: Number(f.vad_threshold),
  hardsub_interval_sec: Number(f.hardsub_interval_sec),
})

export default function TranscribeStage({ mediaSlot, media, file, busy, onJobStarted }: Props) {
  const { dramaId, drama } = useStage()
  const mossEnabled = useMossExperimental()
  const [config, setConfig] = useState<TranscribeConfig | null>(null)
  const [cf, setCf] = useState<ConfigForm | null>(null)
  const [saved, setSaved] = useState(false)
  // Restored from sessionStorage (per drama) so switching stage tabs keeps the form.
  const [restored] = useState(() => loadSourceForm(dramaId))
  const [language, setLanguage] = useState(restored.language ?? drama.source_language ?? 'zh')
  const [script, setScript] = useState(restored.script ?? '')
  const [transcriptText, setTranscriptText] = useState(restored.transcriptText ?? '')
  const [runDiarize, setRunDiarize] = useState(restored.runDiarize ?? false)
  const [speakers, setSpeakers] = useState(restored.speakers ?? '')
  const [minSpeakers, setMinSpeakers] = useState(restored.minSpeakers ?? '')
  const [maxSpeakers, setMaxSpeakers] = useState(restored.maxSpeakers ?? '')
  // Names added to the automatic prompt (kept per drama); the full override is not kept.
  const [extraNames, setExtraNames] = useState(restored.extraNames ?? '')
  const [override, setOverride] = useState('')
  const [useGpu, setUseGpu] = useState<boolean | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  // A refused option, shown on its own field instead of a generic banner.
  const [fieldProblem, setFieldProblem] = useState<RunFieldProblem | null>(null)
  const panelRef = useRef<HTMLElement>(null)
  const transcriptRef = useRef<HTMLTextAreaElement>(null)
  // D03/D06: the last run's speaker count and the hand-corrected speakers.
  const [diar, setDiar] = useState<DiarizationConfig | null>(null)
  const [diarReloads, setDiarReloads] = useState(0)
  const [overwriteManual, setOverwriteManual] = useState(false)
  const [overwriteAck, setOverwriteAck] = useState(false)
  // Only an untouched form (nothing kept for this drama) takes the last run's count.
  const seedSpeakers = useRef(restored.speakers === undefined)
  // D04: the stored media's length, for the time estimates (null = unknown).
  const [duration, setDuration] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    getTranscribeConfig(dramaId).then(
      (c) => {
        if (cancelled) return
        setConfig(c)
        setCf(formFromConfig(c))
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  useEffect(() => {
    let cancelled = false
    getDiarizationConfig(dramaId).then(
      (c) => {
        if (cancelled) return
        setDiar(c)
        if (seedSpeakers.current) {
          seedSpeakers.current = false
          // 0 is "auto", the same as blank.
          if (c.expected_speakers) setSpeakers((cur) => (cur.trim() ? cur : String(c.expected_speakers)))
        }
      },
      () => undefined, // advisory: without it the speaker count stays blank and corrections are kept
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, diarReloads])

  // A finished job may have changed the speakers: re-read the counts.
  const wasBusy = useRef(busy)
  useEffect(() => {
    if (wasBusy.current && !busy) setDiarReloads((n) => n + 1)
    wasBusy.current = busy
  }, [busy])

  useEffect(() => {
    saveSourceForm(dramaId, { language, script, transcriptText, runDiarize, speakers, minSpeakers, maxSpeakers, extraNames })
  }, [dramaId, language, script, transcriptText, runDiarize, speakers, minSpeakers, maxSpeakers, extraNames])

  useEffect(() => {
    let cancelled = false
    getSettings().then(
      (s) => !cancelled && setUseGpu(s.use_gpu),
      () => undefined, // the GPU note is informational; skip it if settings can't load
    )
    return () => {
      cancelled = true
    }
  }, [])

  // The raw novel feeds the automatic prompt: refresh only that, keeping unsaved form edits.
  const reloadAutoPrompt = () => {
    getTranscribeConfig(dramaId).then(
      (c) => setConfig((cur) => (cur ? { ...cur, auto_initial_prompt: c.auto_initial_prompt } : c)),
      () => undefined,
    )
  }

  // Open the folded sections around the flagged field and bring it into view.
  useEffect(() => {
    if (!fieldProblem) return
    const el = panelRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')
    if (!el) return
    for (let d = el.closest('details'); d; d = d.parentElement?.closest('details') ?? null) d.open = true
    el.scrollIntoView({ block: 'center' })
    el.focus()
  }, [fieldProblem])

  const flag = (p: RunFieldProblem | null) => {
    setFieldProblem(p)
  }
  // A refused run or save: name the option when the server's sentence does, else show the banner.
  const fail = (e: unknown) => {
    const p = runProblemFromError(e)
    if (p) {
      setError(null)
      flag(p)
    } else setError(e)
  }
  const fieldError = (f: RunField) => (fieldProblem?.field === f ? fieldProblem.message : null)

  const setC = <K extends keyof ConfigForm>(k: K, v: ConfigForm[K]) => {
    setSaved(false)
    setFieldProblem(null)
    setCf((s) => (s ? { ...s, [k]: v } : s))
  }

  const haveTranscript = config?.transcript_mode === 'have_transcript'
  const hasMedia = !!media && (media.has_audio || media.has_source_video)

  // ffprobe on the stored file (read-only); without it the captions say less.
  useEffect(() => {
    if (!hasMedia) return
    let cancelled = false
    analyzeMedia(dramaId).then(
      (a) => !cancelled && setDuration(a.duration_seconds > 0 ? a.duration_seconds : null),
      () => !cancelled && setDuration(null),
    )
    return () => {
      cancelled = true
    }
    // `media` is re-read after an upload or removal, so a replaced file is measured again.
  }, [dramaId, hasMedia, media])
  // What the primary button still needs, in words (empty = ready).
  const needed = !config || !media
    ? ''
    : !file && !hasMedia && !haveTranscript
      ? 'an audio or video file'
      : haveTranscript && !transcriptText.trim()
        ? 'the transcript text'
        : ''

  // Validates the options; null means "ok" (problem is set otherwise).
  const checkConfig = (): TranscribeConfigUpdate | null => {
    if (!cf) return null
    const update = toUpdate(cf)
    const bad = validateConfig(update)
    setProblem(bad)
    return bad ? null : update
  }

  const saveOptions = () => {
    const update = checkConfig()
    if (!update) return
    updateTranscribeConfig(dramaId, update).then(
      (c) => {
        setError(null)
        setSaved(true)
        setConfig(c)
      },
      fail,
    )
  }

  // Builds the run body, or reports why it can't (returns null).
  const buildRequest = (): TranscribeRunRequest | null => {
    const expected = parseExpectedSpeakers(speakers)
    if (expected === null) {
      setProblem('Expected speakers must be a whole number from 0 to 20.')
      return null
    }
    // The Min/Max range goes with speaker detection after transcribing too.
    const hints = runDiarize ? parseSpeakerHints(speakers, minSpeakers, maxSpeakers) : null
    if (typeof hints === 'string') {
      setProblem(hints)
      return null
    }
    if (haveTranscript && !transcriptText.trim()) {
      setProblem('Paste the transcript first: this drama transcribes from a transcript you supply.')
      return null
    }
    setProblem(null)
    setFieldProblem(null)
    return {
      source_language: language,
      ...(language === 'zh' && script ? { chinese_script: script } : {}),
      ...(haveTranscript ? { transcript_text: transcriptText } : {}),
      run_diarize: runDiarize,
      ...(expected !== undefined ? { expected_speakers: expected } : {}),
      ...(hints?.min !== undefined ? { min_speakers: hints.min } : {}),
      ...(hints?.max !== undefined ? { max_speakers: hints.max } : {}),
      ...promptFields(override, extraNames),
    }
  }

  const transcribe = () => {
    const req = buildRequest()
    if (!req || !config || !cf) return
    const update = checkConfig()
    if (!update) return
    const refused = runOptionProblem(config.transcript_mode, cf.alignment_method, cf.asr_backend_choice, mossEnabled)
    if (refused) {
      flag(refused)
      return
    }
    const start = () => (file ? uploadAndTranscribe(dramaId, file, req) : startTranscribe(dramaId, req))
    // Auto-save changed options first so the run uses what the form shows.
    const current = toUpdate(formFromConfig(config))
    const changed = (Object.keys(update) as (keyof TranscribeConfigUpdate)[]).some((k) => update[k] !== current[k])
    const saveFirst = changed
      ? updateTranscribeConfig(dramaId, update).then((c) => {
          setConfig(c)
          setSaved(true)
        })
      : Promise.resolve()
    saveFirst.then(start).then((r) => {
      setError(null)
      onJobStarted(r.job_id)
    }, fail)
  }

  const diarize = () => {
    const hints = parseSpeakerHints(speakers, minSpeakers, maxSpeakers)
    if (typeof hints === 'string') {
      setProblem(hints)
      return
    }
    setProblem(null)
    const overwrite = manualCount > 0 && overwriteManual && overwriteAck
    startDiarization(dramaId, {
      expectedSpeakers: hints.expected,
      minSpeakers: hints.min,
      maxSpeakers: hints.max,
      ...(overwrite ? { overwriteManual: true } : {}),
    }).then((r) => {
      setError(null)
      setOverwriteManual(false)
      setOverwriteAck(false)
      onJobStarted(r.job_id)
    }, setError)
  }
  const manualCount = diar?.manual_speaker_count ?? 0
  const needsAck = manualCount > 0 && overwriteManual && !overwriteAck
  const corrections = `${manualCount} speaker correction${manualCount === 1 ? '' : 's'}`

  const select = (
    label: string,
    key: 'alignment_method' | 'asr_backend_choice' | 'separation_backend' | 'hardsub_ocr_backend',
    options: string[],
    help?: string,
    disabled: string[] = [],
  ) =>
    cf && (
      <Field label={label} help={help} error={key === 'alignment_method' || key === 'asr_backend_choice' ? fieldError(key) : null}>
        <select value={cf[key]} onChange={(e) => setC(key, e.target.value)}>
          {(options.includes(cf[key]) ? options : [cf[key], ...options]).map((o) => (
            <option key={o} value={o} disabled={disabled.includes(o) && o !== cf[key]}>{optionLabel(o)}</option>
          ))}
        </select>
      </Field>
    )
  const num = (label: string, key: 'beam_size' | 'min_silence_ms' | 'vad_threshold' | 'hardsub_interval_sec', step: number, help: string, unit?: string) =>
    cf && (
      <Field label={label} help={help} unit={unit}>
        <input type="number" step={step} value={cf[key]} onChange={(e) => setC(key, e.target.value)} />
      </Field>
    )
  const toggle = (label: string, key: 'separate_vocals_first' | 'realign_long_segments' | 'whisper_fast_mode' | 'use_groq', help?: string) =>
    cf && (
      <Field label={label} help={help}>
        <Toggle checked={cf[key]} onChange={(v) => setC(key, v)} />
      </Field>
    )
  // Rule 22: the reason's fix focuses the missing field.
  const fixNeeded = () => {
    if (needed === 'the transcript text') return transcriptRef.current?.focus()
    const input = document.getElementById(mediaFileInputId(dramaId))
    if (input) {
      input.scrollIntoView({ block: 'center' })
      input.focus()
    } else {
      // "From a URL" is showing: switch back to the file picker.
      document.querySelector<HTMLInputElement>('.source-from input[type="radio"]')?.click()
    }
  }

  const turboWarning = cf ? whisperModelWarning(cf.whisper_size, language) : ''
  // Only runs that go through Whisper (plain ASR, or aligning a pasted
  // transcript with whisper_diff); a file picked but not uploaded yet has no
  // known length.
  const whisperRun = !!cf && (config?.transcript_mode === 'whisper'
    ? cf.asr_backend_choice === 'whisper'
    : config?.transcript_mode === 'have_transcript' && cf.alignment_method === 'whisper_diff')
  // Undefined (an older server) counts as installed.
  const notInstalled = whisperRun && config?.whisper_installed === false
  const estimate = cf && whisperRun && !file && hasMedia
    ? transcribeEstimate({
        audioSeconds: duration,
        whisperSize: cf.whisper_size,
        useGpu,
        fastMode: cf.whisper_fast_mode,
        modelCached: config?.whisper_model_cached,
        measuredSpeed: config?.whisper_size === cf.whisper_size ? config.measured_speed : null,
        useGroq: cf.use_groq,
        detectSpeakers: runDiarize,
      })
    : null

  return (
    <section className="panel source-panel" aria-label="Transcribe" ref={panelRef}>
      <h3>Transcribe</h3>
      <TranscriptModePicker onChanged={(m) => setConfig((c) => (c ? { ...c, transcript_mode: m } : c))} />
      {mediaSlot}
      <div className="source-grid">
        <Field label="Source language">
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="zh">Chinese</option>
            <option value="ja">Japanese</option>
            <option value="ko">Korean</option>
          </select>
        </Field>
        {language === 'zh' && (
          <Field label="Chinese script">
            <select value={script} onChange={(e) => setScript(e.target.value)}>
              <option value="">Keep current</option>
              <option value="simplified">Simplified (Mainland)</option>
              <option value="traditional">Traditional (Taiwan, Hong Kong)</option>
            </select>
          </Field>
        )}
      </div>
      {haveTranscript && (
        <Field label="Transcript text">
          <textarea ref={transcriptRef} rows={4} value={transcriptText} onChange={(e) => setTranscriptText(e.target.value)} />
        </Field>
      )}
      <div className="setting-list">
        <Field label="Detect speakers after transcribing">
          <Toggle checked={runDiarize} onChange={setRunDiarize} />
        </Field>
      </div>
      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={busy || !cf || !!needed}
          aria-describedby={[notInstalled && 'transcribe-not-installed', needed && !busy && 'transcribe-needed'].filter(Boolean).join(' ') || undefined}
          onClick={transcribe}
        >
          Transcribe
        </button>
        {estimate && !busy && <span className="muted" role="note" data-testid="transcribe-estimate">{estimate}</span>}
        {cf?.separate_vocals_first && whisperRun && !busy && (
          <span className="muted" role="note" data-testid="separation-note">Vocal separation adds time, a lot on CPU.</span>
        )}
      </div>
      {notInstalled && (
        <div className="source-needed" id="transcribe-not-installed" role="note">
          <span>
            <strong>Transcription isn't installed yet.</strong> It turns audio or video into subtitles and is a
            large download. Install it from Diagnostics (you'll see the size and confirm first).
          </span>
          <ButtonLink variant="primary" size="sm" className="button-link" href="#/diagnostics?install=transcription">
            Install transcription
          </ButtonLink>
        </div>
      )}
      {needed && !busy && (
        <div className="source-needed" id="transcribe-needed" role="note">
          <span>Still needed: {needed}.</span>
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={fixNeeded}>
            {needed === 'the transcript text' ? 'Paste transcript' : 'Choose a file'}
          </button>
        </div>
      )}
      {busy && (
        <p className="muted" role="status">
          A job for this drama is already running. Wait for it to finish or cancel it before starting another.
        </p>
      )}
      {(fieldProblem || problem) && (
        <p className="error" role="alert">{fieldProblem ? 'Fix the highlighted option, then transcribe again.' : problem}</p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />

      {cf && (
        <p className="muted source-summary" data-testid="settings-summary">
          {cf.whisper_size} · {LANGUAGE_NAMES[language] ?? language}
          {useGpu !== null && <span data-testid="gpu-note"> · GPU: {useGpu ? 'on' : 'off'} - change in <a href="#/settings">Settings</a></span>}
        </p>
      )}

      <Section
        storageKey="source.transcribe"
        title="More options"
        summary={`${cf?.whisper_size ?? 'model'} · speakers and tuning`}
      >
      <div className="source-grid">
        {cf && (
          <Field label="Whisper model" help={config && !config.whisper_model_cached ? 'This model will be downloaded on first use.' : undefined}>
            <select value={cf.whisper_size} onChange={(e) => setC('whisper_size', e.target.value)}>
              {(WHISPER_SIZES.includes(cf.whisper_size) ? WHISPER_SIZES : [cf.whisper_size, ...WHISPER_SIZES]).map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </Field>
        )}
      </div>
      {turboWarning && <p className="muted" role="note">{turboWarning}</p>}
      <Section storageKey="source.speakers" title="Speakers" summary={speakersSummary(speakers, minSpeakers, maxSpeakers)}>
        <div className="source-grid">
          <Field label="Expected speakers" help="0-20. Blank lets the app decide." error={fieldError('speakers')}>
            <input type="number" value={speakers} onChange={(e) => { setFieldProblem(null); setSpeakers(e.target.value) }} />
          </Field>
          <Field label="Min speakers" help="1-20. When you know a range but not the exact count. Used by Detect speakers only and by detecting speakers after transcribing.">
            <input type="number" min={1} max={20} value={minSpeakers} onChange={(e) => { setFieldProblem(null); setMinSpeakers(e.target.value) }} />
          </Field>
          <Field label="Max speakers" help="1-20. Leave Expected speakers blank when using a range.">
            <input type="number" min={1} max={20} value={maxSpeakers} onChange={(e) => { setFieldProblem(null); setMaxSpeakers(e.target.value) }} />
          </Field>
        </div>
        {manualCount > 0 && (
          <div className="source-manual" data-testid="manual-speakers">
            <div className="setting-list">
              <Field
                label={`Replace my ${corrections}`}
                help="Off keeps your corrections: detection only changes the lines you haven't corrected. On replaces them with what detection finds."
              >
                <Toggle
                  checked={overwriteManual}
                  onChange={(v) => {
                    setOverwriteManual(v)
                    setOverwriteAck(false)
                  }}
                />
              </Field>
            </div>
            {overwriteManual ? (
              <label className="inline stage-ack">
                <input type="checkbox" checked={overwriteAck} onChange={(e) => setOverwriteAck(e.target.checked)} />{' '}
                I understand my {corrections} will be replaced
              </label>
            ) : (
              <p className="muted">Your {corrections} {manualCount === 1 ? 'is' : 'are'} kept.</p>
            )}
          </div>
        )}
        <div className="actions">
          <button
            type="button"
            className={buttonClass('ghost')}
            disabled={busy || needsAck}
            aria-describedby={needsAck ? 'diarize-needed' : undefined}
            onClick={diarize}
          >
            Detect speakers only
          </button>
          {needsAck ? (
            <span className="muted" id="diarize-needed">Still needed: tick the confirmation above, or turn Replace off.</span>
          ) : (
            hasMedia && <span className="muted" data-testid="diarize-estimate">{diarizeEstimate(duration)}</span>
          )}
        </div>
        <DiarizationDeviceNote dramaId={dramaId} refreshKey={busy} />
      </Section>

      {cf && (
        <Section
          storageKey="source.advanced"
          title="Advanced"
          summary={readableSummary(advancedSummary({ ...cf, prompt: override }))}
        >
          <div className="source-grid">
            {num('Beam size', 'beam_size', 1, '1-10. Higher is slower and a little more accurate.')}
            {num('Min silence', 'min_silence_ms', 50, '300-3000. Silence that splits lines; longer gives fewer, longer lines. Auto-tune below can pick it.', 'ms')}
            {num('VAD threshold', 'vad_threshold', 0.05, '0.1-0.9. Higher ignores more quiet sound.')}
            {num('Hardsub interval', 'hardsub_interval_sec', 0.1, '0.5-3.0. How often video frames are read for on-screen text.', 's')}
            {select('Alignment method', 'alignment_method', ['whisper_diff', 'qwen3_forced_align'],
              haveTranscript
                ? 'Qwen3 forced alignment lines up the transcript you supply against the audio for more exact timing.'
                : 'Forced alignment lines up a transcript you provide; for raw audio, pick Whisper or Qwen3-ASR.',
              haveTranscript ? [] : ['qwen3_forced_align'])}
            {select('ASR backend', 'asr_backend_choice', asrBackendOptions(mossEnabled), mossEnabled ? 'MOSS is experimental: it transcribes and labels speakers in one pass, replacing Whisper and speaker detection for this drama.' : undefined)}
            {select('Separation backend', 'separation_backend', ['auto', 'audio_separator', 'demucs'], 'Used when vocals are separated first.')}
            {select('Hardsub OCR', 'hardsub_ocr_backend', ['tesseract', 'paddle'])}
          </div>
          <p className="muted" data-testid="auto-prompt">
            {config?.auto_initial_prompt
              ? `Automatic prompt, from glossary and novel: ${config.auto_initial_prompt}`
              : 'Automatic prompt: no glossary names or raw novel yet. Extra names below still help.'}
          </p>
          <Field
            label="Extra names to expect"
            help="Added to the automatic prompt. Separate names with 、 or commas."
          >
            <input
              value={extraNames}
              placeholder="沈清疑、云隐宗"
              onChange={(e) => setExtraNames(e.target.value)}
            />
          </Field>
          <details>
            <summary>Advanced: replace the automatic prompt</summary>
            <Field
              label="Replacement prompt"
              help="Used instead of the automatic prompt and extra names. Leave empty to keep the automatic one."
            >
              <input value={override} onChange={(e) => setOverride(e.target.value)} />
            </Field>
          </details>
          <div className="setting-list">
            {toggle(
              'Separate vocals first',
              'separate_vocals_first',
              'Removes background music before transcribing. Fast on a GPU; on the CPU it adds a long wait (often many times the clip length). The job shows its progress and whether it runs on GPU or CPU.',
            )}
            {toggle('Realign long segments', 'realign_long_segments')}
            {toggle('Whisper fast mode', 'whisper_fast_mode')}
            {toggle('Use Groq', 'use_groq')}
          </div>
          <div className="actions">
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={saveOptions}>Save options</button>
            {saved && <span role="status" className="muted">Saved.</span>}
          </div>
          <AutoTune
            hasAudio={!!media?.has_audio}
            busy={busy}
            override={override}
            extraNames={extraNames}
            onApplied={(c) => {
              setConfig(c)
              // Keep any other unsaved edits; only min silence changed.
              setCf((cur) => (cur ? { ...cur, min_silence_ms: String(c.min_silence_ms) } : formFromConfig(c)))
            }}
          />
        </Section>
      )}
      </Section>
      <NovelFilePanel kind="raw" busy={busy} onChanged={reloadAutoPrompt} />
    </section>
  )
}
