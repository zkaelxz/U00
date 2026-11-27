import { useState } from 'react'

import { getPresets } from '../../../api/library'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { GroupedPicker } from '../../../components/GroupedPicker'
import { humanize } from '../../../components/labels'
import { NOTHING_STARTS_HELP } from '../../../helpText'
import type { LibraryPreset } from '../../../types/library'
import type {
  TranslatePresetApplied,
  TranslateRunConfig,
  WorkflowTierApplied,
} from '../../../types/translateStage'
import { applyStart, parseStartValue, startOptions } from './startFrom'
import { skippedNote } from '../translateForm'
import { useStage } from '../StageContext'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'

function appliedText(t: WorkflowTierApplied): string {
  const model = t.engine_model ? ` (${t.engine_model})` : ''
  const name = t.tier.charAt(0).toUpperCase() + t.tier.slice(1)
  const qc = t.auto_qc ? ` ${name} recommends Auto QC; run it from the Export stage.` : ''
  return `Applied ${t.label}: ${humanize('engine', t.translation_engine)}${model}, Reflect ${t.reflect ? 'on' : 'off'}.${qc} Nothing has started.`
}

// The Style select doubles as the way to start from a tier or a saved preset:
// choosing one of those applies it at once (it never starts a run) and the
// select keeps showing the form's style, which the applied values may have changed.
export function StyleField({ config, style, onStyleChange, onTierApplied, onPresetApplied }: {
  config: TranslateRunConfig
  style: string
  onStyleChange: (key: string) => void
  onTierApplied: (t: WorkflowTierApplied) => void
  onPresetApplied: (p: TranslatePresetApplied) => void
}) {
  const { dramaId } = useStage()
  const [presets, setPresets] = useState<LibraryPreset[] | null>(null)
  const [applied, setApplied] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  useCancellableEffect((cancelled) => {
    getPresets().then(
      (r) => !cancelled() && setPresets(r.items),
      () => !cancelled() && setPresets([]), // optional here; the Library shows its own error
    )
  }, [])
  const groups = startOptions(config.workflow_tiers, presets, config.engines)
  const apply = (value: string) => {
    setPending(true)
    applyStart(dramaId, value)
      .then(
        (r) => {
          if (!r) return
          setError(null)
          if (r.kind === 'tier') {
            setApplied(appliedText(r.tier))
            onTierApplied(r.tier)
          } else {
            setApplied(`Applied profile "${r.preset.name}". Nothing has started.${skippedNote(r.preset.skipped)}`)
            onPresetApplied(r.preset)
          }
        },
        setError,
      )
      .finally(() => setPending(false))
  }
  return (
    <>
      <Field label="Style" help={`What the translator should sound like. Each choice fills in engine, model and other settings together, and you can still edit them. Draft is DeepSeek; Standard is Claude Sonnet; Release is Claude Opus with Reflect and Auto QC. My presets are saved in the Library. ${NOTHING_STARTS_HELP}`}>
        <GroupedPicker
          title="Style"
          value={style}
          disabled={pending}
          groups={[
            { label: 'Styles', options: config.style_presets.map((p) => ({ value: p.key, label: p.label })) },
            ...(groups.tiers.length > 0 ? [{ label: 'Start from a tier', options: groups.tiers }] : []),
            ...(groups.presets.length > 0 ? [{ label: 'Start from my profile', options: groups.presets }] : []),
          ]}
          onPick={(value) => {
            setApplied(null)
            if (parseStartValue(value)) apply(value)
            else onStyleChange(value)
          }}
        />
      </Field>
      {(applied || error != null) && (
        <div className="translate-basics-wide">
          {applied && <span className="muted" role="status">{applied}</span>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </div>
      )}
    </>
  )
}
