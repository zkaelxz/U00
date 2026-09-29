import { useEffect, useState, type ReactNode } from 'react'

import { getSettings } from '../../../api/settings'
import {
  getTranscribeConfig,
  startDiarization,
  startTranscribe,
  updateTranscribeConfig,
  uploadAndTranscribe,
} from '../../../api/workspace'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import type {
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
  saveSourceForm,
  validateConfig,
  whisperModelWarning,
} from '../sourceForm'
import { useStage } from '../StageContext'
import { AutoTune } from './AutoTune'
import { NovelFilePanel } from './NovelFilePanel'
import { promptFields } from './transcribePrompt'
import './source.css'

const WHISPER_SIZES = ['tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo']
const LANGUAGE_NAMES: Record<string, string> = { zh: 'Chinese', ja: 'Japanese', ko: 'Korean' }

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

  const setC = <K extends keyof ConfigForm>(k: K, v: ConfigForm[K]) => {
    setSaved(false)
    setCf((s) => (s ? { ...s, [k]: v } : s))
  }

  const haveTranscript = config?.transcript_mode === 'have_transcript'
  const hasMedia = !!media && (media.has_audio || media.has_source_video)
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
      setError,
    )
  }

  // Builds the run body, or reports why it can't (returns null).
  const buildRequest = (): TranscribeRunRequest | null => {
    const expected = parseExpectedSpeakers(speakers)
    if (expected === null) {
      setProblem('Expected speakers must be a whole number from 0 to 20.')
      return null
    }
    if (runDiarize && (minSpeakers.trim() || maxSpeakers.trim())) {
      setProblem('A speaker range works with "Detect speakers only". Clear Min/Max speakers, or use Expected speakers, to detect speakers after transcribing.')
      return null
    }
    if (haveTranscript && !transcriptText.trim()) {
      setProblem('Paste the transcript first: this drama transcribes from a transcript you supply.')
      return null
    }
    setProblem(null)
    return {
      source_language: language,
      ...(language === 'zh' && script ? { chinese_script: script } : {}),
      ...(haveTranscript ? { transcript_text: transcriptText } : {}),
      run_diarize: runDiarize,
      ...(expected !== undefined ? { expected_speakers: expected } : {}),
      ...promptFields(override, extraNames),
    }
  }

  const transcribe = () => {
    const req = buildRequest()
    if (!req || !config || !cf) return
    const update = checkConfig()
    if (!update) return
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
    }, setError)
  }

  const diarize = () => {
    const hints = parseSpeakerHints(speakers, minSpeakers, maxSpeakers)
    if (typeof hints === 'string') {
      setProblem(hints)
      return
    }
    setProblem(null)
    startDiarization(dramaId, { expectedSpeakers: hints.expected, minSpeakers: hints.min, maxSpeakers: hints.max }).then((r) => {
      setError(null)
      onJobStarted(r.job_id)
    }, setError)
  }

  const select = (
    label: string,
    key: 'alignment_method' | 'asr_backend_choice' | 'separation_backend' | 'hardsub_ocr_backend',
    options: string[],
    help?: string,
  ) =>
    cf && (
      <Field label={label} help={help}>
        <select value={cf[key]} onChange={(e) => setC(key, e.target.value)}>
          {(options.includes(cf[key]) ? options : [cf[key], ...options]).map((o) => (
            <option key={o} value={o}>{o}</option>
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
  const check = (label: string, key: 'separate_vocals_first' | 'realign_long_segments' | 'whisper_fast_mode' | 'use_groq') =>
    cf && (
      <label className="check">
        <input type="checkbox" checked={cf[key]} onChange={(e) => setC(key, e.target.checked)} /> {label}
      </label>
    )

  const turboWarning = cf ? whisperModelWarning(cf.whisper_size, language) : ''

  return (
    <section className="panel source-panel" aria-label="Transcribe">
      <h3>Transcribe</h3>
      {mediaSlot}
      <div className="actions">
        <button type="button" className="primary" disabled={busy || !cf || !!needed} onClick={transcribe}>
          Transcribe
        </button>
        <button type="button" disabled={busy} onClick={diarize}>
          Detect speakers only
        </button>
      </div>
      {needed && !busy && <p className="muted source-needed">Still needed: {needed}.</p>}
      {busy && (
        <p className="muted" role="status">
          A job for this drama is already running. Wait for it to finish or cancel it before starting another.
        </p>
      )}
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {cf && (
        <p className="muted source-summary" data-testid="settings-summary">
          {cf.whisper_size} · {LANGUAGE_NAMES[language] ?? language}
          {useGpu !== null && <span data-testid="gpu-note"> · GPU: {useGpu ? 'on' : 'off'} - change in <a href="#/settings">Settings</a></span>}
        </p>
      )}

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
        {cf && (
          <Field label="Whisper model" help={config && !config.whisper_model_cached ? 'This model will be downloaded on first use.' : undefined}>
            <select value={cf.whisper_size} onChange={(e) => setC('whisper_size', e.target.value)}>
              {(WHISPER_SIZES.includes(cf.whisper_size) ? WHISPER_SIZES : [cf.whisper_size, ...WHISPER_SIZES]).map((o) => (
                <option key={o} value={o}>{o}</option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Expected speakers" help="0-20. Blank lets the app decide.">
          <input type="number" value={speakers} onChange={(e) => setSpeakers(e.target.value)} />
        </Field>
        <Field label="Min speakers" help="1-20. For Detect speakers only, when you know a range but not the exact count.">
          <input type="number" min={1} max={20} value={minSpeakers} onChange={(e) => setMinSpeakers(e.target.value)} />
        </Field>
        <Field label="Max speakers" help="1-20. Leave Expected speakers blank when using a range.">
          <input type="number" min={1} max={20} value={maxSpeakers} onChange={(e) => setMaxSpeakers(e.target.value)} />
        </Field>
      </div>
      {turboWarning && <p className="muted" role="note">{turboWarning}</p>}
      {haveTranscript && (
        <Field label="Transcript text">
          <textarea rows={4} value={transcriptText} onChange={(e) => setTranscriptText(e.target.value)} />
        </Field>
      )}
      <label className="check">
        <input type="checkbox" checked={runDiarize} onChange={(e) => setRunDiarize(e.target.checked)} /> Detect speakers after transcribing
      </label>

      {cf && (
        <Section
          storageKey="source.advanced"
          title="Advanced"
          summary={advancedSummary({ ...cf, prompt: override })}
        >
          <div className="source-grid">
            {num('Beam size', 'beam_size', 1, '1-10. Higher is slower and a little more accurate.')}
            {num('Min silence', 'min_silence_ms', 50, '300-3000. Silence that splits lines; longer gives fewer, longer lines. Auto-tune below can pick it.', 'ms')}
            {num('VAD threshold', 'vad_threshold', 0.05, '0.1-0.9. Higher ignores more quiet sound.')}
            {num('Hardsub interval', 'hardsub_interval_sec', 0.1, '0.5-3.0. How often video frames are read for on-screen text.', 's')}
            {select('Alignment method', 'alignment_method', ['whisper_diff', 'qwen3_forced_align'])}
            {select('ASR backend', 'asr_backend_choice', ['whisper', 'qwen3_asr'])}
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
          <div className="source-checks">
            {check('Separate vocals first', 'separate_vocals_first')}
            {check('Realign long segments', 'realign_long_segments')}
            {check('Whisper fast mode', 'whisper_fast_mode')}
            {check('Use Groq', 'use_groq')}
          </div>
          <div className="actions">
            <button type="button" onClick={saveOptions}>Save options</button>
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
      <NovelFilePanel kind="raw" busy={busy} onChanged={reloadAutoPrompt} />
    </section>
  )
}
