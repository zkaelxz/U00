import { useEffect, useState } from 'react'

import { listenApi } from '../../api/listen'
import { getCharacters } from '../../api/translateStage'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { useJob, useJobRun } from '../../hooks/useJob'
import { useReattachJob } from '../../hooks/useReattachJob'
import { useSession } from '../../hooks/useSession'
import { chooseEngine, type ListenConfig } from '../../types/listen'
import type { CharacterEntry } from '../../types/translateStage'
import { jobFailed, jobSucceeded } from '../../types/jobs'
import { holds } from '../diagnostics/adminUsers'
import { dubJobIds } from '../workspace/stageJobIds'

interface Props {
  dramaId: number
  // The narration track now exists: the Reader re-reads its media so the player appears.
  onReady: () => void
}

// Null when narration can be started, otherwise the plain reason it cannot.
function listenBlocker(cfg: ListenConfig, engine: string): string | null {
  if (cfg.speakable_line_count === 0) return 'There is no text to speak yet.'
  if (cfg.blocker) return cfg.blocker
  if (!engine) return 'No voice engine is available.'
  return cfg.tts_engines.find((t) => t.key === engine)?.unavailable_reason ?? null
}

// How a speaker's voice is set up, in words.
function voiceState(c: CharacterEntry): string {
  if (c.has_ref_audio) return 'reference clip'
  if (c.voice_design.trim()) return 'described voice'
  return 'no voice yet'
}

const LANGUAGE_LABEL: Record<string, string> = { translation: 'Translation', original: 'Original language' }

export function ListenPicker({ dramaId, engines, engine, voices, disabled, onChange, languages = [], language = '', onLanguage }: {
  dramaId: number
  engines: ListenConfig['tts_engines']
  engine: string
  voices: CharacterEntry[]
  disabled: boolean
  onChange: (key: string) => void
  languages?: string[]
  language?: string
  onLanguage?: (key: string) => void
}) {
  return (
    <>
      {engines.length > 0 && (
        <label className="field">
          <span>Voice engine</span>
          <select value={engine} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
            {engines.map((t) => (
              <option key={t.key} value={t.key} disabled={t.unavailable_reason != null}>
                {t.label}{t.unavailable_reason ? ` (${t.unavailable_reason})` : ''}
              </option>
            ))}
          </select>
        </label>
      )}
      {languages.length > 1 && onLanguage && (
        <label className="field">
          <span>Voice language</span>
          <select value={language} disabled={disabled} onChange={(e) => onLanguage(e.target.value)}>
            {languages.map((key) => (
              <option key={key} value={key}>{LANGUAGE_LABEL[key] ?? key}</option>
            ))}
          </select>
        </label>
      )}
      {voices.map((v) => (
        <p key={v.speaker_label} className="muted">
          {v.character_name || v.speaker_label}: {voiceState(v)}{' '}
          <a href={`#/drama/${dramaId}/review?tools=change`}>Change voice</a>
        </p>
      ))}
    </>
  )
}

// Starts the narration job through the Listen routes (the engine picked here, the title's language).
// Only a narrated title shows it (the config route is 404 for any other); once a track exists it is offered again, folded, to replace it.
export function ListenSection({ dramaId, onReady }: Props) {
  // The server refuses a start without jobs.start; this only hides a button that would lead to that.
  const canStart = holds(useSession(), 'jobs.start')
  const [cfg, setCfg] = useState<ListenConfig | null>(null)

  useEffect(() => {
    let cancelled = false
    listenApi.config(dramaId).then(
      (c) => !cancelled && setCfg(c),
      () => !cancelled && setCfg(null),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId])

  if (!cfg || !canStart) return null
  return <ListenRun dramaId={dramaId} cfg={cfg} onReady={onReady} remake={cfg.track_available} />
}

// Split from ListenSection so a title that cannot narrate never looks up narration jobs.
// remake: the title already has a track; the pickers stay folded and a run replaces it.
function ListenRun({ dramaId, cfg, onReady, remake }: Props & { cfg: ListenConfig; remake: boolean }) {
  const [error, setError] = useState<unknown>(null)
  const [pickedEngine, setPickedEngine] = useState('')
  const [pickedLanguage, setPickedLanguage] = useState('')
  const [voices, setVoices] = useState<CharacterEntry[]>([])
  useEffect(() => {
    let cancelled = false
    // The voice lines are a convenience; Listen still runs without them.
    getCharacters(dramaId).then((c) => !cancelled && setVoices(c.filter((v) => v.line_count > 0)), () => {})
    return () => {
      cancelled = true
    }
  }, [dramaId])
  const [jobId, setJobId, runKey, adoptJob] = useJobRun()
  useReattachJob(dubJobIds(dramaId), adoptJob)

  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      if (jobSucceeded(j)) onReady()
    },
  })

  const busy = jobId !== null && !done && !pollError
  // This run just made the track: the player in Watch / listen takes over.
  if (!busy && done && jobSucceeded(job)) return remake ? <p className="muted" role="status">The new narration is in the player below.</p> : null
  const engine = chooseEngine(cfg.tts_engines, cfg.default_engine, pickedEngine)
  const narrationLanguage = cfg.narration_language_options.includes(pickedLanguage) ? pickedLanguage : cfg.narration_language
  const blocker = listenBlocker(cfg, engine)
  const failedText = done && job && jobFailed(job) ? job.message : null
  const start = () =>
    listenApi.run(dramaId, { tts_engine: engine, narration_language: narrationLanguage }).then((r) => {
      setError(null)
      setJobId(r.job_id)
    }, setError)

  return (
    <Section title={remake ? 'Make the narration again' : 'Listen'} storageKey={remake ? 'reader.listenAgain' : 'reader.listen'} defaultOpen={!remake} summary={busy ? (job?.message ?? '') : undefined}>
      <ListenPicker dramaId={dramaId} engines={cfg.tts_engines} engine={engine} voices={voices} disabled={busy} onChange={setPickedEngine}
        languages={cfg.narration_language_options} language={narrationLanguage} onLanguage={setPickedLanguage} />
      <div className="actions">
        <button type="button" className="primary" disabled={busy || blocker !== null} onClick={start}>
          {remake ? 'Make again' : 'Listen'}
        </button>
        {(blocker || busy || failedText) && (
          <span className="muted" role="status">{blocker ?? (busy ? (job?.message ?? '') : failedText)}</span>
        )}
      </div>
      <ErrorBanner error={error ?? pollError} onDismiss={() => setError(null)} />
    </Section>
  )
}
