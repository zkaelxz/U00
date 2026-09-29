import { useEffect, useState } from 'react'

import { getSettings } from '../../../api/settings'
import {
  getTranscribeConfig,
  startDiarization,
  startTranscribe,
  updateTranscribeConfig,
  uploadAndTranscribe,
} from '../../../api/workspace'
import { ErrorBanner } from '../../../components/ErrorBanner'
import type {
  TranscribeConfig,
  TranscribeConfigUpdate,
  TranscribeRunRequest,
} from '../../../types/workspace'
import {
  loadSourceForm,
  parseExpectedSpeakers,
  saveSourceForm,
  validateConfig,
  whisperModelWarning,
} from '../sourceForm'
import { useStage } from '../StageContext'

const WHISPER_SIZES = ['tiny', 'base', 'small', 'medium', 'large-v3', 'large-v3-turbo']

interface Props {
  // A pre-checked file chosen in the media panel, or null.
  file: File | null
  busy: boolean
  onJobStarted: (jobId: string) => void
}

function ConfigForm({
  config,
  language,
  onSaved,
}: {
  config: TranscribeConfig
  language: string
  onSaved: (c: TranscribeConfig) => void
}) {
  const { dramaId } = useStage()
  const [f, setF] = useState({
    whisper_size: config.whisper_size,
    alignment_method: config.alignment_method,
    asr_backend_choice: config.asr_backend_choice,
    separation_backend: config.separation_backend,
    hardsub_ocr_backend: config.hardsub_ocr_backend,
    beam_size: String(config.beam_size),
    min_silence_ms: String(config.min_silence_ms),
    vad_threshold: String(config.vad_threshold),
    hardsub_interval_sec: String(config.hardsub_interval_sec),
    separate_vocals_first: config.separate_vocals_first,
    realign_long_segments: config.realign_long_segments,
    whisper_fast_mode: config.whisper_fast_mode,
    use_groq: config.use_groq,
  })
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [saved, setSaved] = useState(false)
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => {
    setSaved(false)
    setF((s) => ({ ...s, [k]: v }))
  }

  const save = () => {
    const update: TranscribeConfigUpdate = {
      ...f,
      beam_size: Number(f.beam_size),
      min_silence_ms: Number(f.min_silence_ms),
      vad_threshold: Number(f.vad_threshold),
      hardsub_interval_sec: Number(f.hardsub_interval_sec),
    }
    const bad = validateConfig(update)
    setProblem(bad)
    if (bad) return
    updateTranscribeConfig(dramaId, update).then(
      (c) => {
        setError(null)
        setSaved(true)
        onSaved(c)
      },
      setError,
    )
  }

  const select = (label: string, key: 'alignment_method' | 'asr_backend_choice' | 'separation_backend' | 'hardsub_ocr_backend' | 'whisper_size', options: string[]) => (
    <label>
      {label}
      <select value={f[key]} onChange={(e) => set(key, e.target.value)}>
        {(options.includes(f[key]) ? options : [f[key], ...options]).map((o) => (
          <option key={o} value={o}>{o}</option>
        ))}
      </select>
    </label>
  )
  const num = (label: string, key: 'beam_size' | 'min_silence_ms' | 'vad_threshold' | 'hardsub_interval_sec', step: number) => (
    <label>
      {label}
      <input type="number" step={step} value={f[key]} onChange={(e) => set(key, e.target.value)} />
    </label>
  )
  const check = (label: string, key: 'separate_vocals_first' | 'realign_long_segments' | 'whisper_fast_mode' | 'use_groq') => (
    <label>
      <input type="checkbox" checked={f[key]} onChange={(e) => set(key, e.target.checked)} /> {label}
    </label>
  )

  return (
    <fieldset className="form-grid">
      <legend>Transcribe options</legend>
      {select('Whisper size', 'whisper_size', WHISPER_SIZES)}
      {whisperModelWarning(f.whisper_size, language) && (
        <p className="muted" role="note">{whisperModelWarning(f.whisper_size, language)}</p>
      )}
      {select('Alignment method', 'alignment_method', ['whisper_diff', 'qwen3_forced_align'])}
      {select('ASR backend', 'asr_backend_choice', ['whisper', 'qwen3_asr'])}
      {select('Vocal separation backend', 'separation_backend', ['auto', 'audio_separator', 'demucs'])}
      {select('Hardsub OCR backend', 'hardsub_ocr_backend', ['tesseract', 'paddle'])}
      {num('Beam size (1-10)', 'beam_size', 1)}
      {num('Min silence ms (300-3000)', 'min_silence_ms', 50)}
      {num('VAD threshold (0.1-0.9)', 'vad_threshold', 0.05)}
      {num('Hardsub interval sec (0.5-3.0)', 'hardsub_interval_sec', 0.1)}
      {check('Separate vocals first', 'separate_vocals_first')}
      {check('Realign long segments', 'realign_long_segments')}
      {check('Whisper fast mode', 'whisper_fast_mode')}
      {check('Use Groq', 'use_groq')}
      <div>
        <button type="button" onClick={save}>Save options</button>
        {saved && <span role="status"> Saved.</span>}
      </div>
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </fieldset>
  )
}

export default function TranscribeStage({ file, busy, onJobStarted }: Props) {
  const { dramaId, drama } = useStage()
  const [config, setConfig] = useState<TranscribeConfig | null>(null)
  // Restored from sessionStorage (per drama) so switching stage tabs keeps the form.
  const [restored] = useState(() => loadSourceForm(dramaId))
  const [language, setLanguage] = useState(restored.language ?? drama.source_language ?? 'zh')
  const [script, setScript] = useState(restored.script ?? '')
  const [transcriptText, setTranscriptText] = useState(restored.transcriptText ?? '')
  const [runDiarize, setRunDiarize] = useState(restored.runDiarize ?? false)
  const [speakers, setSpeakers] = useState(restored.speakers ?? '')
  const [prompt, setPrompt] = useState(restored.prompt ?? '')
  const [useGpu, setUseGpu] = useState<boolean | null>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getTranscribeConfig(dramaId).then(
      (c) => !cancelled && setConfig(c),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  useEffect(() => {
    saveSourceForm(dramaId, { language, script, transcriptText, runDiarize, speakers, prompt })
  }, [dramaId, language, script, transcriptText, runDiarize, speakers, prompt])

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

  const haveTranscript = config?.transcript_mode === 'have_transcript'

  // Builds the run body, or reports why it can't (returns null).
  const buildRequest = (): TranscribeRunRequest | null => {
    const expected = parseExpectedSpeakers(speakers)
    if (expected === null) {
      setProblem('Expected speakers must be a whole number from 0 to 20.')
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
      initial_prompt: prompt,
    }
  }

  const run = (start: (req: TranscribeRunRequest) => Promise<{ job_id: string }>) => {
    const req = buildRequest()
    if (!req) return
    start(req).then((r) => {
      setError(null)
      onJobStarted(r.job_id)
    }, setError)
  }

  const diarize = () => {
    const expected = parseExpectedSpeakers(speakers)
    if (expected === null) {
      setProblem('Expected speakers must be a whole number from 0 to 20.')
      return
    }
    setProblem(null)
    startDiarization(dramaId, expected).then((r) => {
      setError(null)
      onJobStarted(r.job_id)
    }, setError)
  }

  return (
    <section className="panel" aria-label="Transcribe">
      <h3>Transcribe</h3>
      {config && (
        <p className="muted">
          Mode: {config.transcript_mode}. Whisper model {config.whisper_model_cached ? 'is downloaded' : 'will be downloaded on first use'}.
        </p>
      )}
      {config && <ConfigForm config={config} language={language} onSaved={setConfig} />}
      <fieldset className="form-grid">
        <legend>Run options</legend>
        <label>
          Source language
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="zh">Chinese</option>
            <option value="ja">Japanese</option>
            <option value="ko">Korean</option>
          </select>
        </label>
        {language === 'zh' && (
          <label>
            Chinese script
            <select value={script} onChange={(e) => setScript(e.target.value)}>
              <option value="">Keep current</option>
              <option value="simplified">Simplified (Mainland)</option>
              <option value="traditional">Traditional (Taiwan, Hong Kong)</option>
            </select>
          </label>
        )}
        {haveTranscript && (
          <label>
            Transcript text
            <textarea rows={4} value={transcriptText} onChange={(e) => setTranscriptText(e.target.value)} />
          </label>
        )}
        <label>
          Initial prompt
          <input value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </label>
        <label>
          Expected speakers (0-20, blank = auto)
          <input type="number" value={speakers} onChange={(e) => setSpeakers(e.target.value)} />
        </label>
        <label>
          <input type="checkbox" checked={runDiarize} onChange={(e) => setRunDiarize(e.target.checked)} /> Detect speakers after transcribing
        </label>
        {useGpu !== null && (
          <p className="muted" data-testid="gpu-note">
            GPU: {useGpu ? 'on' : 'off'} - change in <a href="#/settings">Settings</a>
          </p>
        )}
        {busy && (
          <p className="muted" role="status">
            A job for this drama is already running. Wait for it to finish or cancel it before starting another.
          </p>
        )}
        <div className="actions">
          <button type="button" disabled={busy} onClick={() => run((r) => startTranscribe(dramaId, r))}>
            Start transcription
          </button>
          <button
            type="button"
            disabled={busy || !file}
            onClick={() => file && run((r) => uploadAndTranscribe(dramaId, file, r))}
          >
            Upload selected file and transcribe
          </button>
          <button type="button" disabled={busy} onClick={diarize}>
            Detect speakers only
          </button>
        </div>
      </fieldset>
      {problem && <p className="error" role="alert">{problem}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </section>
  )
}
