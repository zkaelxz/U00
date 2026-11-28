import { useEffect, useMemo, useRef, useState } from 'react'

import { getDiarizationConfig, getTranscribeConfig } from '../../../api/workspace'
import { useResetUndo } from '../../../hooks/useResetUndo'
import { changedKeys, isRecord, pickDraft, useStageDraft } from '../../../hooks/useStageDraft'
import type { DiarizationConfig, TranscribeConfig } from '../../../types/workspace'
import { mergeRunChoices, runChoicesFromConfig, TRANSCRIBE_DRAFT_SHAPE, TRANSCRIBE_DRAFT_STAGE } from '../sourceForm'
import { defaultSeparateVocals, formFromConfig, sendsSeparateVocals, type ConfigForm } from './transcribeConfigForm'

interface Options {
  dramaId: number
  defaultLanguage: string
  busy: boolean
  onLoadError: (e: unknown) => void
  // Stage-only state that goes back with the form (refused-option messages).
  onResetStage: () => void
}

// The Transcribe form's values, their saved draft and the config they start from, so the stage and the Tools panel share one object.
export function useTranscribeForm({ dramaId, defaultLanguage, busy, onLoadError, onResetStage }: Options) {
  const [config, setConfig] = useState<TranscribeConfig | null>(null)
  const [cf, setCf] = useState<ConfigForm | null>(null)
  // Whether the user changed the separate-vocals switch since the options loaded or were saved;
  // until then a title that never chose follows the model's default (transcribeConfigForm.ts).
  const [separateTouched, setSeparateTouched] = useState(false)
  // The form as last left for this drama, so a stage-tab switch or a reload keeps it.
  const { draft: restored, raw: rawDraft, save: saveDraft, clear: clearDraft } = useStageDraft(dramaId, TRANSCRIBE_DRAFT_STAGE, TRANSCRIBE_DRAFT_SHAPE)
  const [language, setLanguage] = useState(restored.language ?? defaultLanguage)
  const [script, setScript] = useState(restored.script ?? '')
  const [transcriptText, setTranscriptText] = useState(restored.transcriptText ?? '')
  const [runDiarize, setRunDiarize] = useState(restored.runDiarize ?? false)
  const [speakers, setSpeakers] = useState(restored.speakers ?? '')
  const [minSpeakers, setMinSpeakers] = useState(restored.minSpeakers ?? '')
  const [maxSpeakers, setMaxSpeakers] = useState(restored.maxSpeakers ?? '')
  const [extraNames, setExtraNames] = useState(restored.extraNames ?? '')
  const [override, setOverride] = useState(restored.override ?? '')
  // The last run's speaker count and the hand-corrected speakers.
  const [diar, setDiar] = useState<DiarizationConfig | null>(null)
  const [diarReloads, setDiarReloads] = useState(0)
  const [overwriteManual, setOverwriteManual] = useState(false)
  const [overwriteAck, setOverwriteAck] = useState(false)
  // Only an untouched form (nothing kept for this drama) takes the last run's count.
  const seedSpeakers = useRef(restored.speakers === undefined)

  useEffect(() => {
    let cancelled = false
    getTranscribeConfig(dramaId).then(
      (c) => {
        if (cancelled) return
        setConfig(c)
        // The Advanced values changed here and not run yet sit on top of the saved options.
        const saved = formFromConfig(c)
        const advanced = rawDraft?.advanced
        const kept = pickDraft(isRecord(advanced) ? advanced : null, saved)
        setCf({ ...saved, ...kept })
        // The draft keeps the switch only once it was touched.
        setSeparateTouched('separate_vocals_first' in kept)
        // Language, speaker detection, names and prompt are kept on the title; only an edit made here and not run or saved stays on top.
        const start = mergeRunChoices(runChoicesFromConfig(c, defaultLanguage), restored, rawDraft?.base)
        setLanguage(start.language)
        setRunDiarize(start.runDiarize)
        setSpeakers((cur) => start.speakers || cur) // the last speaker-detection run may have filled it first
        setMinSpeakers(start.minSpeakers)
        setMaxSpeakers(start.maxSpeakers)
        setExtraNames(start.extraNames)
        setOverride(start.override)
        if (start.speakers) seedSpeakers.current = false
      },
      (e: unknown) => !cancelled && onLoadError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, rawDraft, restored, defaultLanguage]) // eslint-disable-line react-hooks/exhaustive-deps -- onLoadError only reports

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

  const followsDefault = !!config && !sendsSeparateVocals(config, separateTouched)
  const shownCf = useMemo(
    () => (cf && config && followsDefault ? { ...cf, separate_vocals_first: defaultSeparateVocals(config, cf) } : cf),
    [cf, config, followsDefault],
  )

  useEffect(() => {
    // Until the saved options arrive the stored Advanced changes are kept as they are.
    const changed = shownCf && config ? changedKeys(shownCf, formFromConfig(config)) : null
    if (changed && followsDefault) delete changed.separate_vocals_first
    const advanced = changed ?? rawDraft?.advanced
    // `base` is the saved choices written beside the form's, so a later visit can tell an edit from a stale copy.
    const base = config ? runChoicesFromConfig(config, defaultLanguage) : rawDraft?.base
    saveDraft({ language, script, transcriptText, runDiarize, speakers, minSpeakers, maxSpeakers, extraNames, override, advanced, base })
  }, [saveDraft, rawDraft, shownCf, config, followsDefault, defaultLanguage, language, script, transcriptText, runDiarize, speakers, minSpeakers, maxSpeakers, extraNames, override])

  // Back to the drama's language and the saved options; the draft for this title is dropped.
  const resetUndo = useResetUndo()
  const resetToDefaults = () => {
    const prev = { language, script, transcriptText, runDiarize, speakers, minSpeakers, maxSpeakers, extraNames, override, cf, separateTouched }
    resetUndo.reset(() => {
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
      onResetStage()
      if (config) setCf(formFromConfig(config))
      setSeparateTouched(false)
    }, () => {
      setLanguage(prev.language)
      setScript(prev.script)
      setTranscriptText(prev.transcriptText)
      setRunDiarize(prev.runDiarize)
      setSpeakers(prev.speakers)
      setMinSpeakers(prev.minSpeakers)
      setMaxSpeakers(prev.maxSpeakers)
      setExtraNames(prev.extraNames)
      setOverride(prev.override)
      setCf(prev.cf)
      setSeparateTouched(prev.separateTouched)
    })
  }

  return {
    config, setConfig, cf: shownCf, setCf, separateTouched, setSeparateTouched,
    language, setLanguage, script, setScript, transcriptText, setTranscriptText,
    runDiarize, setRunDiarize, speakers, setSpeakers, minSpeakers, setMinSpeakers, maxSpeakers, setMaxSpeakers,
    extraNames, setExtraNames, override, setOverride,
    diar, overwriteManual, setOverwriteManual, overwriteAck, setOverwriteAck,
    resetUndo, resetToDefaults,
  }
}

export type TranscribeForm = ReturnType<typeof useTranscribeForm>
