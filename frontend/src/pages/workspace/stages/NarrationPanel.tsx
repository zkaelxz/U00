import { useEffect, useState } from 'react'

import { narrationApi } from '../../../api/dub'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize } from '../../../components/labels'
import { Section } from '../../../components/Section'
import { Toggle } from '../../../components/Toggle'
import { buttonClass } from '../../../components/uiClasses'
import type { NarrationConfig } from '../../../types/dub'
import { stageLabel } from '../stages'
import { useNovelFilesVersion } from './novelFileEvents'
import { EngineGateNote } from './EngineGateNote'
import { TagChoices } from './TagChoices'
import { EMPTY_TAG_CHOICE, effectiveTagEngine, tagChoiceProblem, tagRunBody, type TagChoice } from './tagChoice'
import { useEngineGate, useTitleEngines } from './useEngineGate'

interface Props {
  dramaId: number
  busy: boolean
  onJobStarted: (jobId: string) => void
}

// Only novel-narration titles show this panel, and their first stage is named as a novel's.
const ATTACH_NOVEL = `Attach the novel text on the ${stageLabel('source', 'novel')} stage first`

export function NarrationPanel({ dramaId, busy, onJobStarted }: Props) {
  const [cfg, setCfg] = useState<NarrationConfig | null>(null)
  const [choice, setChoice] = useState<TagChoice>(EMPTY_TAG_CHOICE)
  const [blocked, setBlocked] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  // Not remembered: resuming an interrupted run is the default.
  const [startOver, setStartOver] = useState(false)
  const [error, setError] = useState<unknown>(null)

  // Attaching the text bumps this, so the panel stops saying there is none.
  const filesVersion = useNovelFilesVersion()
  useEffect(() => {
    let cancelled = false
    narrationApi.config(dramaId).then(
      (c) => !cancelled && setCfg(c),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, filesVersion])

  const engine = cfg ? effectiveTagEngine(choice, cfg) : ''
  // The title's engine list knows the SDK and Ollama; this panel's own key flags are the ones its run checks.
  const titleEngines = useTitleEngines(dramaId)
  const ownEngines = cfg?.engines.map((o) => ({ name: o.key, label: o.key, free: false, models: null, key_configured: o.key_configured }))
  const titleGate = useEngineGate(titleEngines.engines, engine)
  const ownGate = useEngineGate(ownEngines, engine)
  // A missing key does not block this run: it cuts the text and marks every line Narrator.
  const found = titleGate ?? ownGate
  const gate = choice.oneNarrator || found?.id === 'engine_key' ? null : found

  if (!cfg) return error ? <ErrorBanner error={error} /> : null
  if (!cfg.is_narration) return null

  const partial = !!(choice.from.trim() || choice.to.trim())
  // Chosen chapters replace only their own lines, so there is nothing to confirm.
  const needsConfirm = cfg.replaces_existing_lines && !partial
  const disabled =
    busy || cfg.job_running || !cfg.has_novel_source || !engine || !!gate || !!blocked || (needsConfirm && !confirmed)

  const start = () => {
    const bad = tagChoiceProblem(choice)
    if (bad) return setError(new Error(bad))
    return narrationApi.run(dramaId, tagRunBody(choice, cfg.default_engine), undefined, { fresh: startOver }).then((r) => {
      setError(null)
      setConfirmed(false)
      onJobStarted(r.job_id)
    }, setError)
  }

  const scope = partial ? ` · chapters ${choice.from.trim() || 1}-${choice.to.trim() || cfg.chapter_count}` : ''
  const summary = !cfg.has_novel_source
    ? ATTACH_NOVEL
    : cfg.job_running
      ? 'A chunk-and-tag job is already running'
      : choice.oneNarrator
        ? `One narrator${scope}`
        : `${engine ? humanize('engine', engine) : 'no engine'}${choice.model.trim() ? ` · ${choice.model.trim()}` : ''}${scope}`

  return (
    <Section storageKey="dub.narration" title="Chunk and tag speakers" summary={summary}>
      {!cfg.has_novel_source && <p className="muted">{ATTACH_NOVEL}.</p>}
      <TagChoices dramaId={dramaId} cfg={cfg} value={choice} onChange={setChoice} onBlocked={setBlocked} />
      <EngineGateNote gate={gate} />
      {needsConfirm && (
        <div className="setting-list">
          <Field label={`Replace the ${cfg.existing_line_count} existing lines`}>
            <Toggle checked={confirmed} onChange={setConfirmed} />
          </Field>
        </div>
      )}
      <div className="setting-list">
        <Field label="Start over" help="Ignore the batches an interrupted run already tagged and tag everything again.">
          <Toggle checked={startOver} onChange={setStartOver} />
        </Field>
      </div>
      {cfg.job_running && <p className="muted">A chunk-and-tag job is already running.</p>}
      {blocked && <p className="muted">{blocked}</p>}
      <div className="dub-actions">
        <button type="button" className={buttonClass('secondary')} disabled={disabled} onClick={start}>
          Chunk and tag
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Section>
  )
}
