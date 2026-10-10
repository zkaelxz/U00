import { useEffect, useRef, useState, type MutableRefObject, type ReactNode } from 'react'

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
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanizeValue } from '../../../components/labels'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { changedKeys, isRecord, pickDraft, useStageDraft } from '../../../hooks/useStageDraft'
import type {
  DiarizationConfig,
  MediaStatus,
  TranscribeConfig,
  TranscribeConfigUpdate,
  TranscribeRunRequest,
} from '../../../types/workspace'
import {
  advancedSummary,
  MIN_SILENCE_MS_MAX,
  MIN_PAUSE_SEC_MAX,
  MIN_PAUSE_SEC_MIN,
  MIN_SILENCE_MS_MIN,
  parseExpectedSpeakers,
  parseSpeakerHints,
  runOptionProblem,
  runProblemFromError,
  TRANSCRIBE_DRAFT_SHAPE,
  TRANSCRIBE_DRAFT_STAGE,
  validateConfig,
  type RunField,
  type RunFieldProblem,
  whisperModelWarning,
} from '../sourceForm'
import { useDeveloperMode } from '../../assistant/developerMode'
import { PreflightCard } from '../../preflight/PreflightCard'
import { useStage } from '../StageContext'
import { AutoTune } from './AutoTune'
import { DiarizationDeviceNote } from './DiarizationDeviceNote'
import { NovelFilePanel } from './NovelFilePanel'
import { SpeechCoverage } from './SpeechCoverage'
import { TranscriptModePicker } from './SourceModes'
import { mediaFileInputId, needsReplaceConfirm } from './stageBlockers'
import { asrBackendHelp, GROQ_HELP, withoutUntouchedBackend } from './transcribeBackendField'
import { diarizeEstimate, measuredRunSeconds, transcribeEstimate } from './transcribeEstimate'
import { promptFields } from './transcribePrompt'
import { VoiceDetectorField } from './VoiceDetectorField'
import './source.css'

const WHISPER_SIZES = ['tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo']
// The value stays the model name; the text says which is the default and its Japanese/Korean caveat.
const WHISPER_LABELS: Record<string, string> = {
  'large-v3-turbo': 'large-v3-turbo (default)',
}
const LANGUAGE_NAMES: Record<string, string> = { zh: 'Chinese', ja: 'Japanese', ko: 'Korean' }
// Display names for the Advanced backend choices (the option value stays raw).
const OPTION_LABELS: Record<string, string> = {
  whisper_diff: 'Whisper (diff)',
  qwen3_forced_align: 'Qwen3 forced alignment',
  whisper: 'Whisper',
  qwen3_asr: 'Qwen3 ASR',
  qwen3_asr_vad: 'Qwen3 ASR with speech detection (no Whisper)',
  qwen3_asr_long: 'Qwen3 ASR on long windows (no Whisper)',
  auto: 'Automatic',
  normal: 'Normal (default)',
  sensitive: 'More sensitive',
  audio_separator: 'Audio separator',
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
  // The picker's "Replace the current audio/video" box, sent with an upload of `file`.
  confirmReplace: boolean
  // The drama has audio/video and that box is not ticked yet.
  replaceUnconfirmed: boolean
  // The server refused the upload until replacing is confirmed.
  onReplaceRefused: () => void
  busy: boolean
  // expectedSeconds: this PC's recorded speed applied to this media, when there is one.
  // sentFile: the run was started by uploading `file`.
  onJobStarted: (jobId: string, expectedSeconds?: number | null, sentFile?: boolean) => void
  // Set to the Transcribe action, so the Source stage's Last run card can run it again with this form.
  retryRef?: MutableRefObject<(() => void) | null>
}

type ConfigForm = {
  whisper_size: string
  alignment_method: string
  asr_backend_choice: string
  separation_backend: string
  hardsub_ocr_backend: string
  beam_size: string
  min_silence_ms: string
  min_pause_sec: string
  vad_threshold: string
  sensitivity_preset: string
  hallucination_silence_sec: string
  hardsub_interval_sec: string
  separate_vocals_first: boolean
  realign_long_segments: boolean
  whisper_fast_mode: boolean
  whisper_repeat_guard: boolean
  split_by_sentences: boolean
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
  min_pause_sec: String(c.min_pause_sec),
  vad_threshold: String(c.vad_threshold),
  sensitivity_preset: c.sensitivity_preset,
  hallucination_silence_sec: String(c.hallucination_silence_sec),
  hardsub_interval_sec: String(c.hardsub_interval_sec),
  separate_vocals_first: c.separate_vocals_first,
  realign_long_segments: c.realign_long_segments,
  whisper_fast_mode: c.whisper_fast_mode,
  whisper_repeat_guard: c.whisper_repeat_guard ?? false,
  split_by_sentences: c.split_by_sentences ?? false,
  use_groq: c.use_groq,
})

const toUpdate = (f: ConfigForm): TranscribeConfigUpdate => ({
  ...f,
  beam_size: Number(f.beam_size),
  min_silence_ms: Number(f.min_silence_ms),
  min_pause_sec: Number(f.min_pause_sec),
  vad_threshold: Number(f.vad_threshold),
  hallucination_silence_sec: Number(f.hallucination_silence_sec),
  hardsub_interval_sec: Number(f.hardsub_interval_sec),
})

export default function TranscribeStage({
  mediaSlot, media, file, confirmReplace, replaceUnconfirmed, onReplaceRefused, busy, onJobStarted, retryRef,
}: Props) {
  const { dramaId, drama } = useStage()
  const developerMode = useDeveloperMode()
  const [config, setConfig] = useState<TranscribeConfig | null>(null)
  const [cf, setCf] = useState<ConfigForm | null>(null)
  const [saved, setSaved] = useState(false)
  // The form as last left for this drama, so a stage-tab switch or a reload keeps it.
  const { draft: restored, raw: rawDraft, save: saveDraft, clear: clearDraft } = useStageDraft(dramaId, TRANSCRIBE_DRAFT_STAGE, TRANSCRIBE_DRAFT_SHAPE)
  const defaultLanguage = drama.source_language ?? 'zh'
  const [language, setLanguage] = useState(restored.language ?? defaultLanguage)
  const [script, setScript] = useState(restored.script ?? '')
  const [transcriptText, setTranscriptText] = useState(restored.transcriptText ?? '')
  const [runDiarize, setRunDiarize] = useState(restored.runDiarize ?? false)
  const [speakers, setSpeakers] = useState(restored.speakers ?? '')
  const [minSpeakers, setMinSpeakers] = useState(restored.minSpeakers ?? '')
  const [maxSpeakers, setMaxSpeakers] = useState(restored.maxSpeakers ?? '')
  const [extraNames, setExtraNames] = useState(restored.extraNames ?? '')
  const [override, setOverride] = useState(restored.override ?? '')
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
  // Set by a transcription started here: the coverage panel checks its result once the job ends.
  const [checkAfterRun, setCheckAfterRun] = useState(false)

  useEffect(() => {
    let cancelled = false
    getTranscribeConfig(dramaId).then(
      (c) => {
        if (cancelled) return
        setConfig(c)
        // The Advanced values changed here and not run yet sit on top of the saved options.
        const saved = formFromConfig(c)
        const advanced = rawDraft?.advanced
        setCf({ ...saved, ...pickDraft(isRecord(advanced) ? advanced : null, saved) })
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, rawDraft])

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
    // Until the saved options arrive the stored Advanced changes are kept as they are.
    const advanced = cf && config ? changedKeys(cf, formFromConfig(config)) : rawDraft?.advanced
    saveDraft({ language, script, transcriptText, runDiarize, speakers, minSpeakers, maxSpeakers, extraNames, override, advanced })
  }, [saveDraft, rawDraft, cf, config, language, script, transcriptText, runDiarize, speakers, minSpeakers, maxSpeakers, extraNames, override])

  // Back to the drama's language and the saved options; the draft for this title is dropped.
  const resetToDefaults = () => {
    clearDraft()
    setLanguage(defaultLanguage)
    setScript('')
    setTranscriptText('')
    setRunDiarize(false)
    setSpeakers(diar?.expected_speakers ? String(diar.expected_speakers) : '')
    setMinSpeakers('')
    setMaxSpeakers('')
    setExtraNames('')
    setOverride('')
    setProblem(null)
    setFieldProblem(null)
    if (config) setCf(formFromConfig(config))
  }

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

  // The card installed Whisper: take the server's new answer without touching unsaved form edits.
  const reloadConfigWhenReady = (ok: boolean) => {
    if (!ok) return
    getTranscribeConfig(dramaId).then(
      (c) => setConfig((cur) => (cur ? { ...cur, whisper_installed: c.whisper_installed } : c)),
      () => undefined,
    )
  }

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
    // Only fields that are always in the DOM are flagged (runProblemFromError); a hidden one shows the banner text instead.
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
    if (needsReplaceConfirm(e)) onReplaceRefused()
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
  // A staged file that Replace is not ticked for is not part of this run: the stored media is transcribed.
  const uploadFile = file && !replaceUnconfirmed ? file : null

  // Validates the options; null means "ok" (problem is set otherwise).
  const checkConfig = (): TranscribeConfigUpdate | null => {
    if (!cf) return null
    const update = config ? withoutUntouchedBackend(toUpdate(cf), config.asr_backend_choice) : toUpdate(cf)
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
    const refused = runOptionProblem(config.transcript_mode, cf.alignment_method)
    if (refused) {
      flag(refused)
      return
    }
    // A file picked but not uploaded yet has no known length, and a cloud run's time isn't this PC's.
    const speed = config.whisper_size === cf.whisper_size ? config.measured_speed : null
    const expectedRunSeconds = whisperRun && !uploadFile && !cf.use_groq
      ? measuredRunSeconds({
          audioSeconds: duration, whisperSize: cf.whisper_size, useGpu, measuredSpeed: speed,
          measuredStages: speed ? config.measured_stage_seconds : undefined, separateVocals: cf.separate_vocals_first,
          realignLong: cf.realign_long_segments,
        })
      : null
    const start = () => (uploadFile ? uploadAndTranscribe(dramaId, uploadFile, req, confirmReplace) : startTranscribe(dramaId, req))
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
      setCheckAfterRun(true)
      onJobStarted(r.job_id, expectedRunSeconds, !!uploadFile)
    }, fail)
  }

  useEffect(() => {
    if (retryRef) retryRef.current = transcribe
  })

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
    key: 'alignment_method' | 'asr_backend_choice' | 'separation_backend' | 'hardsub_ocr_backend' | 'sensitivity_preset',
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
  const num = (label: string, key: 'beam_size' | 'min_silence_ms' | 'min_pause_sec' | 'vad_threshold' | 'hallucination_silence_sec' | 'hardsub_interval_sec', step: number, help: string, unit?: string) =>
    cf && (
      <Field label={label} help={help} unit={unit}>
        <input type="number" step={step} value={cf[key]} onChange={(e) => setC(key, e.target.value)} />
      </Field>
    )
  const toggle = (label: string, key: 'separate_vocals_first' | 'realign_long_segments' | 'whisper_fast_mode' | 'whisper_repeat_guard' | 'split_by_sentences' | 'use_groq', help?: string) =>
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
  const estimate = cf && whisperRun && !uploadFile && hasMedia
    ? transcribeEstimate({
        audioSeconds: duration,
        whisperSize: cf.whisper_size,
        useGpu,
        fastMode: cf.whisper_fast_mode,
        modelCached: config?.whisper_model_cached,
        measuredSpeed: config?.whisper_size === cf.whisper_size ? config.measured_speed : null,
        measuredRuns: config?.measured_speed_runs,
        measuredStages: config?.whisper_size === cf.whisper_size ? config.measured_stage_seconds : undefined,
        separateVocals: cf.separate_vocals_first,
        realignLong: cf.realign_long_segments,
        measuredDiarizeSpeed: config?.measured_diarize_speed,
        measuredDiarizeRuns: config?.measured_diarize_runs,
        useGroq: cf.use_groq,
        detectSpeakers: runDiarize,
      })
    : null

  return (
    <section className="panel source-panel" aria-label="Transcribe" ref={panelRef}>
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
        {cf && (
          <Field label="Whisper model" help={config && !config.whisper_model_cached ? 'This model will be downloaded on first use.' : undefined}>
            <select value={cf.whisper_size} onChange={(e) => setC('whisper_size', e.target.value)}>
              {(WHISPER_SIZES.includes(cf.whisper_size) ? WHISPER_SIZES : [cf.whisper_size, ...WHISPER_SIZES]).map((o) => (
                <option key={o} value={o}>{WHISPER_LABELS[o] ?? o}</option>
              ))}
            </select>
          </Field>
        )}
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
      {turboWarning && <p className="muted" role="note">{turboWarning}</p>}
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
          disabled={busy || !cf || !!needed || notInstalled}
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
        <div id="transcribe-not-installed">
          <PreflightCard needs={['whisper', 'ffmpeg', 'gpu']} whisperInstalled={false} onReady={reloadConfigWhenReady} />
        </div>
      )}
      {file && replaceUnconfirmed && !busy && (
        <p className="muted" role="note" data-testid="transcribe-staged-unused">
          The chosen file is not used for this run. Transcribe uses the current audio/video; tick "Replace the current
          audio/video" to use the new file.
        </p>
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

      {cf ? (
        <p className="muted source-summary" data-testid="settings-summary">
          Whisper {cf.whisper_size} · {LANGUAGE_NAMES[language] ?? language}
          {useGpu !== null && <span data-testid="gpu-note"> · GPU: {useGpu ? 'on' : 'off'} - change in <a href="#/settings">Settings</a></span>}
        </p>
      ) : (
        <p className="muted source-summary" aria-hidden="true">&nbsp;</p>
      )}

      {/* Always mounted so the fold's header never appears late; its body waits for the saved options. */}
      <Section
          storageKey="source.advanced"
          title="More options"
          summary={cf ? [readableSummary(advancedSummary({ ...cf, prompt: override, source_language: language }, developerMode)), `speakers ${speakersSummary(speakers, minSpeakers, maxSpeakers)}`].join(' · ') : 'tuning'}
        >
          {cf && <>
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
              hasMedia && <span className="muted" data-testid="diarize-estimate">{diarizeEstimate(duration, config?.measured_diarize_speed, config?.measured_diarize_runs)}</span>
            )}
          </div>
          <DiarizationDeviceNote dramaId={dramaId} refreshKey={busy} />
          <div className="source-grid">
            {select('Sensitivity', 'sensitivity_preset', ['normal', 'sensitive'],
              'Catches quieter or faster speech, but may add false text on music or breathing.')}
            {num('Min silence', 'min_silence_ms', 50, `${MIN_SILENCE_MS_MIN}-${MIN_SILENCE_MS_MAX}. Silence that splits lines; longer gives fewer, longer lines. Lower values split at shorter pauses and can cut mid-sentence. Auto-tune below can pick it.`, 'ms')}
            {num('Pause that can split a long line', 'min_pause_sec', 0.05, `${MIN_PAUSE_SEC_MIN}-${MIN_PAUSE_SEC_MAX}. Longer lines are only cut where the speaker pauses at least this long. Higher gives fewer, longer lines. Lower cuts more.`, 's')}
            {select('Alignment method', 'alignment_method', ['whisper_diff', 'qwen3_forced_align'],
              haveTranscript
                ? 'Qwen3 forced alignment lines up the transcript you supply against the audio for more exact timing.'
                : 'Forced alignment lines up a transcript you provide; for raw audio, pick Whisper or Qwen3-ASR.',
              haveTranscript ? [] : ['qwen3_forced_align'])}
            {select('ASR backend', 'asr_backend_choice', asrBackendOptions(), [asrBackendHelp(asrBackendOptions()), config?.asr_backend_notice].filter(Boolean).join('\n'))}
            <VoiceDetectorField />
            {developerMode && num('Beam size', 'beam_size', 1, '1-10. Higher is slower and a little more accurate.')}
            {developerMode && num('VAD threshold', 'vad_threshold', 0.05, '0.1-0.9. Higher ignores more quiet sound.')}
            {developerMode && num('Hallucination guard', 'hallucination_silence_sec', 0.5, 'Experimental. Off (0) by default; 0 or 0.5-10. Titles that were at exactly 2.0, the old default, were reset to 0 once. Whisper skips a line with this much silence inside it, which stops invented text over silence or music. Lower is stricter and can drop real lines after a pause. Whisper only: ignored by Qwen3-ASR, and by Fast mode.', 's')}
            {developerMode && num('Hardsub interval', 'hardsub_interval_sec', 0.1, '0.5-3.0. How often video frames are read for on-screen text.', 's')}
            {developerMode && select('Separation backend', 'separation_backend', ['auto', 'audio_separator', 'demucs'], 'Used when vocals are separated first.')}
            {developerMode && select('Hardsub OCR', 'hardsub_ocr_backend', ['tesseract', 'paddle', 'auto'], 'PaddleOCR reads Chinese, Korean and Japanese captions with the matching language model. Automatic uses it when installed and falls back to Tesseract, with a note.')}
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
              'Removes background music before transcribing. Skip it unless the background is music alone: it hurt with noise and did nothing on clean audio. Fast on a GPU; on the CPU it adds a long wait (often many times the clip length).',
            )}
            {toggle('Realign long segments', 'realign_long_segments')}
            {toggle('Whisper fast mode', 'whisper_fast_mode')}
            {toggle(
              'Split lines by sentences',
              'split_by_sentences',
              'Whisper hears longer stretches of speech, then lines are cut at sentence ends and, for long ones, at pauses between words. Min silence is not used. Whisper and Qwen3 ASR only; the speech-detection backends already cut their own lines.'
              + (cf && !['whisper', 'qwen3_asr'].includes(cf.asr_backend_choice)
                ? ' Not used with the selected ASR backend: choose Whisper or Qwen3 ASR for this to apply. Long lines can still be cut afterwards in Review.'
                : ''),
            )}
            {developerMode && toggle(
              'Whisper repeat guard',
              'whisper_repeat_guard',
              'Stops Whisper repeating the same few words. Can drop or change real Chinese and Japanese speech, where short words repeat naturally. Turn on only if a title shows repeated-phrase loops.',
            )}
            {toggle('Use Groq', 'use_groq', GROQ_HELP)}
          </div>
          <div className="actions">
            <button type="button" className={buttonClass('secondary', 'sm')} onClick={saveOptions}>Save options</button>
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={resetToDefaults}>Reset to defaults</button>
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
          </>}
        </Section>
      <SpeechCoverage hasAudio={!!media?.has_audio} busy={busy} autoCheck={checkAfterRun} onAutoChecked={() => setCheckAfterRun(false)} />
      <NovelFilePanel kind="raw" busy={busy} onChanged={reloadAutoPrompt} />
    </section>
  )
}
