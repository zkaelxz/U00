import { useEffect, useState } from 'react'

import { getPresets } from '../../../api/library'
import { applyTranslatePreset, applyWorkflowTier } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { humanize } from '../../../components/labels'
import { buttonClass } from '../../../components/uiClasses'
import { NOTHING_STARTS_HELP } from '../../../helpText'
import type { LibraryPreset } from '../../../types/library'
import type {
  TranslatePresetApplied,
  TranslateRunConfig,
  WorkflowTierApplied,
} from '../../../types/translateStage'
import { savePresetStart } from '../translateForm'
import { useStage } from '../StageContext'

export interface StartOption {
  // "tier:<key>" or "preset:<id>": one select value tells the Apply button which call to make.
  value: string
  label: string
}
export interface StartGroups {
  tiers: StartOption[]
  presets: StartOption[]
}

export function startOptions(
  tiers: TranslateRunConfig['workflow_tiers'] | undefined,
  presets: LibraryPreset[] | null,
): StartGroups {
  return {
    tiers: (tiers ?? []).map((t) => ({ value: `tier:${t.key}`, label: t.label })),
    presets: (presets ?? []).map((p) => ({ value: `preset:${p.id}`, label: p.name })),
  }
}

export function parseStartValue(value: string): { kind: 'tier'; key: string } | { kind: 'preset'; id: number } | null {
  const [kind, rest] = [value.slice(0, value.indexOf(':')), value.slice(value.indexOf(':') + 1)]
  if (kind === 'tier' && rest) return { kind, key: rest }
  if (kind === 'preset' && Number.isInteger(Number(rest)) && rest !== '') return { kind, id: Number(rest) }
  return null
}

export function appliedText(t: WorkflowTierApplied): string {
  const model = t.engine_model ? ` (${t.engine_model})` : ''
  const name = t.tier.charAt(0).toUpperCase() + t.tier.slice(1)
  const qc = t.auto_qc ? ` ${name} recommends Auto QC; run it from the Export stage.` : ''
  return `Applied ${t.label}: ${humanize('engine', t.translation_engine)}${model}, Reflect ${t.reflect ? 'on' : 'off'}.${qc} Nothing has started.`
}

export type StartApplied =
  | { kind: 'tier'; tier: WorkflowTierApplied }
  | { kind: 'preset'; preset: TranslatePresetApplied }

// Saves the chosen starting point on the drama; never starts a run.
export async function applyStart(dramaId: number, value: string): Promise<StartApplied | null> {
  const parsed = parseStartValue(value)
  if (!parsed) return null
  if (parsed.kind === 'tier') return { kind: 'tier', tier: await applyWorkflowTier(dramaId, parsed.key) }
  const preset = await applyTranslatePreset(dramaId, parsed.id)
  // Kept so the preset's values come back on later visits, as for a preset chosen at creation.
  savePresetStart(dramaId, preset)
  return { kind: 'preset', preset }
}

export function StartFromPicker({ config, initialTier, onTierChange, onTierApplied, onPresetApplied }: {
  config: TranslateRunConfig
  initialTier: string | null
  onTierChange: (tier: string) => void
  onTierApplied: (t: WorkflowTierApplied) => void
  onPresetApplied: (p: TranslatePresetApplied) => void
}) {
  const { dramaId } = useStage()
  const [presets, setPresets] = useState<LibraryPreset[] | null>(null)
  const tiers = config.workflow_tiers ?? []
  const [picked, setPicked] = useState(() =>
    initialTier && tiers.some((t) => t.key === initialTier) ? `tier:${initialTier}`
      : tiers.some((t) => t.key === 'standard') ? 'tier:standard' : tiers[0] ? `tier:${tiers[0].key}` : '')
  const [applied, setApplied] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [pending, setPending] = useState(false)
  useEffect(() => {
    let cancelled = false
    getPresets().then(
      (r) => !cancelled && setPresets(r.items),
      () => !cancelled && setPresets([]), // optional here; the Library shows its own error
    )
    return () => {
      cancelled = true
    }
  }, [])
  const groups = startOptions(config.workflow_tiers, presets)
  if (!groups.tiers.length && !groups.presets.length) return null
  const apply = () => {
    setPending(true)
    applyStart(dramaId, picked)
      .then(
        (r) => {
          if (!r) return
          setError(null)
          if (r.kind === 'tier') {
            setApplied(appliedText(r.tier))
            onTierApplied(r.tier)
          } else {
            setApplied(`Applied preset "${r.preset.name}". Nothing has started.`)
            onPresetApplied(r.preset)
          }
        },
        setError,
      )
      .finally(() => setPending(false))
  }
  return (
    <div className="check-row translate-tier">
      <Field label="Start from…" help={`Fills in engine, model and the other settings together, and stays editable. Starting tiers: Draft is DeepSeek; Standard is Claude Sonnet; Release is Claude Opus with Reflect and Auto QC. My presets are the ones saved in the Library. ${NOTHING_STARTS_HELP}`}>
        <select
          value={picked}
          onChange={(e) => {
            setPicked(e.target.value)
            setApplied(null)
            const p = parseStartValue(e.target.value)
            if (p?.kind === 'tier') onTierChange(p.key)
          }}
        >
          {!picked && <option value="">Choose…</option>}
          {groups.tiers.length > 0 && (
            <optgroup label="Starting tiers">
              {groups.tiers.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </optgroup>
          )}
          {groups.presets.length > 0 && (
            <optgroup label="My presets">
              {groups.presets.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </optgroup>
          )}
        </select>
      </Field>
      <button type="button" className={buttonClass('secondary', 'sm')} disabled={pending || !picked} onClick={apply}>Apply</button>
      {applied && <span className="muted" role="status">{applied}</span>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
