import { useEffect, useState } from 'react'

import { updateDramaMetadata } from '../../../api/library'
import { useResetUndo } from '../../../hooks/useResetUndo'
import { useStageDraft } from '../../../hooks/useStageDraft'
import type { TranslateRunConfig } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import {
  applyStartToForm,
  defaultBatchSize,
  initialForm,
  loadPresetStart,
  restoreRunOptions,
  runOptionsDraft,
  TRANSLATE_DRAFT_STAGE,
  type RunForm,
} from '../translateForm'

interface Options {
  config: TranslateRunConfig
  // The Glossary's Re-translate tab runs with this form's settings.
  onForm: (f: RunForm) => void
  // Stage-only state that goes back with the form (the refused-option message).
  onResetStage: () => void
}

// The Translate run form's values with their saved draft and the per-title choices that save as soon as they change.
export function useTranslateRunForm({ config, onForm, onResetStage }: Options) {
  const { dramaId, drama } = useStage()
  const [base] = useState<RunForm>(() => applyStartToForm(initialForm(config, loadPresetStart(dramaId)), config.translate_start, config))
  // What the owner last chose for this title, restored after a reload.
  const { raw: rawDraft, save: saveDraft, clear: clearDraft } = useStageDraft(dramaId, TRANSLATE_DRAFT_STAGE, {})
  const [restored] = useState(() => restoreRunOptions(rawDraft, base, config))
  // Review proposed glossary terms before the run starts.
  const [reviewFirst, setReviewFirst] = useState(restored.reviewFirst && !!drama.series_id)
  const [f, setF] = useState<RunForm>(restored.form)
  useEffect(() => onForm(f), [onForm, f])
  const set = <K extends keyof RunForm>(k: K, v: RunForm[K]) => setF((s) => ({ ...s, [k]: v }))
  useEffect(() => {
    saveDraft(runOptionsDraft({ form: f, baseEngine: config.translation_engine, reviewFirst }, defaultBatchSize(config, f)))
  }, [saveDraft, f, config, reviewFirst])
  // Back to the title's saved engine and the Settings defaults; the draft for this title is dropped.
  const resetUndo = useResetUndo()
  const resetToDefaults = () => {
    const prev = { f, reviewFirst }
    resetUndo.reset(() => {
      clearDraft()
      setF(base)
      setReviewFirst(false)
      onResetStage()
    }, () => {
      setF(prev.f)
      setReviewFirst(prev.reviewFirst)
    })
  }
  // The two prompt toggles are saved for the title as soon as they change, so
  // every later run (retries, glossary re-translation, AI line actions, the
  // CLI) uses what is shown here, not a default.
  const [toggleSaved, setToggleSaved] = useState<boolean | null>(null)
  const saveToggle = (key: 'female_pronouns' | 'genre_notes', v: boolean) => {
    set(key, v)
    setToggleSaved(null)
    const body = key === 'female_pronouns' ? { default_female_pronouns: v } : { include_genre_notes: v }
    updateDramaMetadata(dramaId, body).then(
      () => setToggleSaved(true),
      () => setToggleSaved(false),
    )
  }
  // Per title only (not part of a preset or a run): saved as soon as it changes.
  const [bySentence, setBySentence] = useState<boolean>(config.translate_by_sentence ?? false)
  const [bySentenceSaved, setBySentenceSaved] = useState<boolean | null>(null)
  const saveBySentence = (v: boolean) => {
    setBySentence(v)
    setBySentenceSaved(null)
    updateDramaMetadata(dramaId, { translate_by_sentence: v }).then(
      () => setBySentenceSaved(true),
      () => setBySentenceSaved(false),
    )
  }
  const savedNote =
    toggleSaved === true ? ' Saved for this title; every later run uses it.'
    : toggleSaved === false ? ' Could not save this choice for the title; it applies to the next run only.'
    : config.default_female_pronouns != null || config.include_genre_notes != null
      ? ' Saved for this title; every run uses it.'
      : ''

  return {
    base, f, setF, set, reviewFirst, setReviewFirst, resetUndo, resetToDefaults,
    saveToggle, bySentence, saveBySentence, bySentenceSaved, savedNote,
  }
}
