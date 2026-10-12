import { useEffect, useRef, useState, type MutableRefObject } from 'react'

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
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import { usePersistedState } from '../../../hooks/usePersistedState'
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
  parseExpectedSpeakers,
  parseSpeakerHints,
  runOptionProblem,
  runProblemFromError,
  TRANSCRIBE_DRAFT_SHAPE,
  TRANSCRIBE_DRAFT_STAGE,
  validateConfig,
  DEFAULT_WHISPER_SIZE,
  whisperExclusionReason,
  whisperSizeChoices,
  type RunField,
  type RunFieldProblem,
  whisperModelWarning,
} from '../sourceForm'
import { useDeveloperMode } from '../../assistant/developerMode'
import { PreflightCard } from '../../preflight/PreflightCard'
import { useStage } from '../StageContext'
import type { MediaTab } from '../mediaTabs'
import { titleKind } from '../titleKind'
import { AutoTune } from './AutoTune'
import { MediaTabPanel } from './MediaTabPanel'
import { TranscribeAdvanced } from './TranscribeAdvanced'
import { formFromConfig, toUpdate, type ConfigForm } from './transcribeConfigForm'
import { NovelFilePanel } from './NovelFilePanel'
import { startAsNovel } from './novelFlow'
import { useNovelFilesVersion } from './novelFileEvents'
import { SpeakerDetectionNotice } from './SpeakerDetectionNotice'
import { SpeechCoverage } from './SpeechCoverage'
import { mediaFileInputId, needsReplaceConfirm, replaceBoxId } from './stageBlockers'
import { withoutUntouchedBackend } from './transcribeBackendField'
import { measuredRunSeconds, transcribeEstimate } from './transcribeEstimate'
import { promptFields } from './transcribePrompt'
import { advisorNote, replacementModel, runLine, whisperOnlyControlReason } from './transcribeRun'
import './source.css'

// The value stays the model name; the text says which is the default and its Japanese/Korean caveat.
const WHISPER_LABELS: Record<string, string> = {
  'large-v3': 'large-v3 (default)',
  'large-v3-turbo': 'large-v3-turbo (faster)',
  'ja-1.5b': 'Japanese (whisper-ja 1.5B): anime and games, opt-in, about 3 GB, needs a GPU',
  'kotoba-v2': 'Japanese (kotoba-whisper v2.0): opt-in, about 1.5 GB, fast; line timing only',
}
// Download sizes, mirrored from services/transcribe_pipeline._MODEL_DOWNLOAD_SIZES for the two models the picker offers.
const WHISPER_DOWNLOAD: Record<string, string> = { 'large-v3': 'about 3 GB', 'large-v3-turbo': 'about 1.6 GB' }
function whisperModelHelp(size: string, notDownloaded: boolean, notUsed: string | null, replacement: string | null): string | undefined {
  if (notUsed) return `${notUsed}. This run will use ${replacement}.`
  const parts = [
    notDownloaded ? `This model will be downloaded on first use${WHISPER_DOWNLOAD[size] ? ` (${WHISPER_DOWNLOAD[size]})` : ''}.` : '',
    size === 'ja-1.5b' ? 'Its licence is not declared; use it for personal work.' : '',
    size === 'kotoba-v2' ? 'It gives line timing only. The Qwen3 forced aligner refines it when installed; otherwise the Whisper line times are kept and the job says so.' : '',
  ].filter(Boolean)
  return parts.length ? parts.join(' ') : undefined
}
const FIX_HIGHLIGHTED = 'Fix the highlighted option, then transcribe again.'
const LANGUAGE_NAMES: Record<string, string> = { zh: 'Chinese', ja: 'Japanese', ko: 'Korean' }
interface Props {
  // The selected Media sub-tab; the Transcribe and Tools panels stay mounted and hide when not selected.
  tab: MediaTab
  goTab: (tab: MediaTab) => void
  // Where the lines come from, as the picker on Add media last saved it (null until it changes).
  transcriptMode: string | null
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
  // False when the raw-novel upload is not shown here (no novel on this title).
  showRawNovel?: boolean
  // expectedSeconds: this PC's recorded speed applied to this media, when there is one.
  // sentFile: the run was started by uploading `file`.
  onJobStarted: (jobId: string, expectedSeconds?: number | null, sentFile?: boolean) => void
  // Set to the Transcribe action, so the Source stage's Last run card can run it again with this form.
  retryRef?: MutableRefObject<(() => void) | null>
}

export default function TranscribeStage({
  tab, goTab, transcriptMode, media, file, confirmReplace, replaceUnconfirmed, onReplaceRefused, busy, showRawNovel = true, onJobStarted, retryRef,
}: Props) {
  const { dramaId, drama, refetchDrama } = useStage()
  const developerMode = useDeveloperMode()
  const [config, setConfig] = useState<TranscribeConfig | null>(null)
  const [cf, setCf] = useState<ConfigForm | null>(null)
  const [saved, setSaved] = useState(false)
  // The server does not say whether a title's options were ever saved, so remember a save here; options
  // that already differ from the defaults count as saved too. It only picks which fold opens first.
  const [savedBefore, setSavedBefore] = usePersistedState(`source.optionsSaved.${dramaId}`, false)
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
  const panelRef = useRef<HTMLDivElement>(null)
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

  // A raw novel saved from the Source stage's optional novel row changes the prompt too.
  const novelFilesVersion = useNovelFilesVersion()
  useEffect(() => {
    if (novelFilesVersion > 0 && !showRawNovel) reloadAutoPrompt()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [novelFilesVersion])

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

  // Every flaggable field lives on the Tools tab, so bring it forward for the focus below to land.
  const flag = (p: RunFieldProblem | null) => {
    setFieldProblem(p)
    if (p) goTab('tools')
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

  // The language picker above can leave a model behind that is not recommended for the new language;
  // the server would refuse the run, so switch to the default and say so.
  const [switchedNotice, setSwitchedNotice] = useState<string | null>(null)
  useEffect(() => {
    const reason = cf && whisperExclusionReason(config?.whisper_exclusions, cf.whisper_size, language)
    if (cf && reason) {
      setSwitchedNotice(`${reason} Switched to ${DEFAULT_WHISPER_SIZE}.`)
      setCf((s) => (s ? { ...s, whisper_size: DEFAULT_WHISPER_SIZE } : s))
    }
  }, [cf, config, language])

  const setC = <K extends keyof ConfigForm>(k: K, v: ConfigForm[K]) => {
    setSaved(false)
    setFieldProblem(null)
    if (k === 'whisper_size') setSwitchedNotice(null)
    setCf((s) => (s ? { ...s, [k]: v } : s))
  }

  // The picker on Add media saves its own change; until it changes, the saved config says.
  const mode = transcriptMode ?? config?.transcript_mode
  const haveTranscript = mode === 'have_transcript'
  const optionsSaved = savedBefore || (!!config && advancedSummary({ ...formFromConfig(config), prompt: '', source_language: language }, true) !== 'defaults')
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
  const [adopting, setAdopting] = useState(false)
  // Text with no audio is a novel to translate, not a transcript to line up.
  const asNovel = () => {
    setAdopting(true)
    startAsNovel(dramaId, transcriptText).then(
      () => {
        setError(null)
        setTranscriptText('')
        refetchDrama()
        goTab('add')
      },
      setError,
    ).finally(() => setAdopting(false))
  }
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
        setSavedBefore(true)
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
      setProblem('Paste the transcript first: this title transcribes from a transcript you supply.')
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
    const refused = runOptionProblem(mode ?? config.transcript_mode, cf.alignment_method)
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
          setSavedBefore(true)
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

  // Rule 22: the reason's fix focuses the missing field.
  // The Replace tick sits beside the file picker on Add media.
  const showReplaceBox = () => {
    goTab('add')
    setTimeout(() => document.getElementById(replaceBoxId(dramaId))?.focus(), 50)
  }
  const fixNeeded = () => {
    if (needed === 'the transcript text') return transcriptRef.current?.focus()
    // The file picker is on Add media; give that tab a frame to show before focusing into it.
    goTab('add')
    setTimeout(() => {
      const input = document.getElementById(mediaFileInputId(dramaId))
      if (input) {
        input.scrollIntoView({ block: 'center' })
        input.focus()
      } else {
        // "From a URL" is showing: switch back to the file picker.
        document.querySelector<HTMLInputElement>('[aria-label="Get audio or video"] input[type="radio"]')?.click()
      }
    }, 50)
  }

  const whisperChoices = cf ? whisperSizeChoices(language, cf.whisper_size, config?.whisper_exclusions, config?.selectable_whisper_sizes) : []
  const hiddenChoice = whisperChoices.find((o) => o.hidden)
  // A pasted transcript is aligned, not transcribed, so the ASR backend choice does not apply to it.
  const backendNotUsed = cf && !haveTranscript ? whisperOnlyControlReason(cf.asr_backend_choice, 'model') : null
  const turboWarning = cf ? whisperModelWarning(cf.whisper_size, language) : ''
  // Only runs that go through Whisper (plain ASR, or aligning a pasted
  // transcript with whisper_diff); a file picked but not uploaded yet has no
  // known length.
  const whisperRun = !!cf && (mode === 'whisper'
    ? cf.asr_backend_choice === 'whisper'
    : mode === 'have_transcript' && cf.alignment_method === 'whisper_diff')
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

  // Shown on the tab in front only: the hidden panel keeping the same text would answer a text search twice.
  const feedback = (
    <>
      {(fieldProblem || problem) && (
        <p className="error" role="alert">{fieldProblem ? FIX_HIGHLIGHTED : problem}</p>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
    </>
  )

  return (
    <div ref={panelRef}>
      <MediaTabPanel id="transcribe" active={tab}>
        <div className="source-grid">
          <Field label="Source language">
            <select value={language} onChange={(e) => setLanguage(e.target.value)}>
              <option value="zh">Chinese</option>
              <option value="ja">Japanese</option>
              <option value="ko">Korean</option>
            </select>
          </Field>
          {cf && (
            <Field label="Whisper model" help={whisperModelHelp(cf.whisper_size, !!config && !config.whisper_model_cached, backendNotUsed, replacementModel(cf.asr_backend_choice))}>
              <select value={cf.whisper_size} disabled={!!backendNotUsed} title={backendNotUsed ?? undefined} onChange={(e) => setC('whisper_size', e.target.value)}>
                {whisperChoices.map((o) => (
                  <option key={o.size} value={o.size} disabled={!!o.reason} title={o.reason ?? undefined}>
                    {o.hidden ? `${o.size} (older choice, hidden)` : WHISPER_LABELS[o.size] ?? o.size}{o.reason ? ` (not for ${LANGUAGE_NAMES[language] ?? language})` : ''}
                  </option>
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
        {hiddenChoice && !backendNotUsed && (
          <p className="muted" role="note" data-testid="whisper-hidden-size">
            This title uses {hiddenChoice.size}, which is no longer offered.{' '}
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setC('whisper_size', DEFAULT_WHISPER_SIZE)}>
              Switch to {DEFAULT_WHISPER_SIZE}
            </button>
          </p>
        )}
        {backendNotUsed && (
          <p className="muted" role="note" data-testid="whisper-not-used">
            {backendNotUsed}: this run will use {replacementModel(cf!.asr_backend_choice)}.
          </p>
        )}
        {turboWarning && !backendNotUsed && <p className="muted" role="note">{turboWarning}</p>}
        {cf && !haveTranscript && (
          <p className="muted" role="note" data-testid="transcribe-advisor">{advisorNote(cf.asr_backend_choice, cf.separate_vocals_first)}</p>
        )}
        {(switchedNotice ?? config?.whisper_size_notice) && (
          <p className="muted" role="note" data-testid="whisper-size-notice">{switchedNotice ?? config?.whisper_size_notice}</p>
        )}
        {whisperChoices.filter((o) => o.reason).map((o) => (
          <p key={o.size} className="muted" data-testid="whisper-exclusion-reason">{o.size}: {o.reason}</p>
        ))}
        {haveTranscript && (
          <Field label="Transcript text">
            <textarea ref={transcriptRef} rows={4} value={transcriptText} onChange={(e) => setTranscriptText(e.target.value)} />
          </Field>
        )}
        {haveTranscript && !hasMedia && !file && (
          <p className="muted" role="note" data-testid="transcript-no-audio">
            A transcript is lined up with the audio, and this title has none yet. If this is text to translate, not speech:{' '}
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={adopting || !transcriptText.trim()} onClick={asNovel}>
              Translate this text as a novel
            </button>
          </p>
        )}
        <div className="setting-list">
          <Field label="Detect speakers after transcribing">
            <Toggle checked={runDiarize} onChange={setRunDiarize} />
          </Field>
        </div>
        <SpeakerDetectionNotice dramaId={dramaId} active={runDiarize} />
        {notInstalled && (
          <div id="transcribe-not-installed">
            <PreflightCard needs={['whisper', 'ffmpeg', 'gpu']} whisperInstalled={false} onReady={reloadConfigWhenReady} />
          </div>
        )}
        {cf && (
          <p className="muted" data-testid="run-line">
            {runLine({
              backend: cf.asr_backend_choice, whisperSize: cf.whisper_size, useGroq: cf.use_groq, useGpu,
              gpuRuntime: config?.gpu_runtime.status, haveTranscript, alignmentMethod: cf.alignment_method,
            })}
          </p>
        )}
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
        {file && replaceUnconfirmed && !busy && (
          <p className="muted" role="note" data-testid="transcribe-staged-unused">
            The chosen file is not used for this run. Transcribe uses the current audio/video; tick "Replace the current
            audio/video" on Add media to use the new file.{' '}
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={showReplaceBox}>Open Add media</button>
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
            A job for this title is already running. Wait for it to finish or cancel it before starting another.
          </p>
        )}
        {tab === 'transcribe' && feedback}
        {cf ? (
          <p className="muted source-summary" data-testid="settings-summary">
            {LANGUAGE_NAMES[language] ?? language}
            {useGpu !== null && <span data-testid="gpu-note"> · {useGpu && config?.gpu_runtime.status === 'missing' ? `GPU unavailable: ${config.gpu_runtime.reason ?? 'the CUDA libraries are missing'}` : `GPU: ${useGpu ? 'on' : 'off'}`} - change in <a href="#/settings">Settings</a></span>}
          </p>
        ) : (
          <p className="muted source-summary" aria-hidden="true">&nbsp;</p>
        )}
      </MediaTabPanel>
      <MediaTabPanel id="tools" active={tab}>
        {cf && (
          <TranscribeAdvanced
            dramaId={dramaId}
            cf={cf}
            config={config}
            setC={setC}
            language={language}
            kind={titleKind(drama.media_type, drama.content_mode)}
            developerMode={developerMode}
            haveTranscript={haveTranscript}
            optionsSaved={optionsSaved}
            fieldError={fieldError}
            speakers={{
              expected: speakers,
              min: minSpeakers,
              max: maxSpeakers,
              set: (field, value) => {
                setFieldProblem(null)
                if (field === 'expected') setSpeakers(value)
                else if (field === 'min') setMinSpeakers(value)
                else setMaxSpeakers(value)
              },
            }}
            extraNames={extraNames}
            setExtraNames={setExtraNames}
            override={override}
            setOverride={setOverride}
            manualCount={manualCount}
            corrections={corrections}
            overwriteManual={overwriteManual}
            setOverwriteManual={setOverwriteManual}
            overwriteAck={overwriteAck}
            setOverwriteAck={setOverwriteAck}
            needsAck={needsAck}
            onDiarize={diarize}
            busy={busy}
            hasMedia={hasMedia}
            duration={duration}
            onSave={saveOptions}
            onReset={resetToDefaults}
            saved={saved}
            problem={tab !== 'tools' ? null : fieldProblem ? FIX_HIGHLIGHTED : problem}
            error={tab === 'tools' ? error : null}
            onDismissError={() => setError(null)}
          />
        )}
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
        <SpeechCoverage hasAudio={!!media?.has_audio} busy={busy} autoCheck={checkAfterRun} onAutoChecked={() => setCheckAfterRun(false)} />
        {showRawNovel && <NovelFilePanel kind="raw" busy={busy} onChanged={reloadAutoPrompt} />}
      </MediaTabPanel>
    </div>
  )
}
