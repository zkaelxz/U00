import { useEffect, useState } from 'react'

import { narrationApi } from '../../../api/dub'
import { withSignal } from '../../../api/client'
import { Field } from '../../../components/Field'
import { ModelSelect } from '../../../components/ModelSelect'
import { Toggle } from '../../../components/Toggle'
import { humanize } from '../../../components/labels'
import { AI_ENGINE_LABEL } from '../../../helpText'
import type { NarrationConfig, NarrationEstimate } from '../../../types/dub'
import { CHAPTER_RANGE_MESSAGE, chapterRange } from '../translateForm'
import { effectiveTagEngine, estimateText, needsCostConfirm, type TagChoice } from './tagChoice'
import { useTitleEngines } from './useEngineGate'

interface Props {
  dramaId: number
  cfg: NarrationConfig
  value: TagChoice
  onChange: (next: TagChoice) => void
  disabled?: boolean
  /** Why the run cannot start yet (a spending confirmation is due, or the chapters are wrong), or null. */
  onBlocked: (reason: string | null) => void
  /** Whether the person has accepted the estimated cost of a paid run with no monthly cap. */
  onAccepted?: (accepted: boolean) => void
  /** The Add chapters box: the new text's lines are cut later, so there is no chapter choice and no dollar figure yet. */
  forNewChapters?: boolean
}

/** The choices shared by the Chunk and tag card and the novel Continue button. */
export function TagChoices({ dramaId, cfg, value, onChange, disabled, onBlocked, onAccepted, forNewChapters }: Props) {
  const engine = effectiveTagEngine(value, cfg)
  const titleEngines = useTitleEngines(dramaId)
  const modelEngine = titleEngines.engines?.find((e) => e.name === engine)
  const hasModelList = (modelEngine?.models?.length ?? 0) > 0
  const [estimate, setEstimate] = useState<NarrationEstimate | null>(null)
  const [accepted, setAccepted] = useState(false)
  const set = (patch: Partial<TagChoice>) => onChange({ ...value, ...patch })
  const range = chapterRange(value.from, value.to)

  useEffect(() => {
    setEstimate(null)
    setAccepted(false)
    if (value.oneNarrator || range === null || !cfg.has_novel_source) return
    const ctl = new AbortController()
    narrationApi
      .estimate(dramaId, { engine, model: value.model.trim() || undefined, ...range }, withSignal(ctl.signal))
      .then((e) => !ctl.signal.aborted && setEstimate(e), () => undefined)
    return () => ctl.abort()
    // range is derived from from/to
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dramaId, engine, value.model, value.oneNarrator, value.from, value.to, cfg.has_novel_source])

  const confirmDue = needsCostConfirm(estimate, forNewChapters) && !value.oneNarrator
  const reason = range === null
    ? CHAPTER_RANGE_MESSAGE
    : confirmDue && !accepted
      ? 'Tick the box to accept the estimated cost.'
      : estimate?.monthly_refusal && !value.oneNarrator
        ? "This month's spending cap is used up."
        : null
  useEffect(() => {
    onAccepted?.(accepted)
  }, [accepted, onAccepted])
  useEffect(() => {
    onBlocked(reason)
    // onBlocked is the parent's setter
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reason])

  return (
    <>
      <div className="setting-list">
        <Field label="One narrator, skip speaker tagging" help="One voice reads the whole novel: the text is only split into lines, every line is marked Narrator, and no AI is used or paid for.">
          <Toggle checked={value.oneNarrator} disabled={disabled} onChange={(oneNarrator) => set({ oneNarrator })} />
        </Field>
      </div>
      {!value.oneNarrator && (
        <div className="dub-grid">
          <Field
            label={AI_ENGINE_LABEL}
            help={`Splits the novel into narration lines of up to ${cfg.max_chunk_chars} characters and tags who speaks each one.`}
          >
            <select
              value={engine}
              disabled={disabled}
              onChange={(e) => set({ engine: e.target.value, model: '' })}
            >
              {cfg.engines.map((o) => (
                <option key={o.key} value={o.key}>
                  {humanize('engine', o.key)}
                  {o.key_configured ? '' : ' (no key set)'}
                </option>
              ))}
            </select>
          </Field>
          {hasModelList ? (
            <ModelSelect engine={modelEngine} value={value.model} disabled={disabled} onChange={(model) => set({ model })} help="Optional; leave on Engine default to use the engine's own." />
          ) : (
            <Field label="Model" help="Optional; leave blank for the engine's default.">
              <input value={value.model} disabled={disabled} onChange={(e) => set({ model: e.target.value })} />
            </Field>
          )}
        </div>
      )}
      {cfg.chapter_count > 1 && !forNewChapters && (
        <div className="dub-grid">
          <Field label="First chapter" help={`Only these chapters are cut and tagged (this text has ${cfg.chapter_count}). Leave both blank for all. Other chapters' lines are not touched.`}>
            <input type="number" min={1} max={cfg.chapter_count} placeholder="1" value={value.from} disabled={disabled} onChange={(e) => set({ from: e.target.value })} />
          </Field>
          <Field label="Last chapter" help="The same number as the first does just that chapter.">
            <input type="number" min={1} max={cfg.chapter_count} placeholder={String(cfg.chapter_count)} value={value.to} disabled={disabled} onChange={(e) => set({ to: e.target.value })} />
          </Field>
        </div>
      )}
      {estimate && !value.oneNarrator && !forNewChapters && <p className="muted" role="status">{estimateText(estimate)}</p>}
      {value.oneNarrator && <p className="muted">No AI is used and nothing is spent.</p>}
      {confirmDue && (
        <div className="setting-list">
          <Field label="No monthly spending cap is set: accept this cost" help="Set a monthly cap in Settings to stop paid runs at a limit.">
            <Toggle checked={accepted} disabled={disabled} onChange={setAccepted} />
          </Field>
        </div>
      )}
    </>
  )
}
