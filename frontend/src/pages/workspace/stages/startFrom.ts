// Starting-point options for the Style select and the call that applies one to a title.
import { engineOffReason } from '../../../api/translate'
import { applyTranslatePreset, applyWorkflowTier } from '../../../api/translateStage'
import type { LibraryPreset } from '../../../types/library'
import type { TranslateEngine } from '../../../types/translate'
import type { TranslatePresetApplied, TranslateRunConfig, WorkflowTierApplied } from '../../../types/translateStage'
import { engineGate } from './engineReady'

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
  engines?: TranslateEngine[],
): StartGroups {
  // A tier whose engine can't run yet says why in the list, as the engine pickers do ("Standard (no key)").
  const offNote = (name: string) => {
    const engine = engines?.find((e) => e.name === name)
    return engine && engineGate(engines, name, null) ? ` (${engineOffReason(engine)})` : ''
  }
  return {
    tiers: (tiers ?? []).map((t) => ({ value: `tier:${t.key}`, label: `${t.label}${offNote(t.translation_engine)}` })),
    presets: (presets ?? []).map((p) => ({ value: `preset:${p.id}`, label: p.name })),
  }
}

export function parseStartValue(value: string): { kind: 'tier'; key: string } | { kind: 'preset'; id: number } | null {
  const [kind, rest] = [value.slice(0, value.indexOf(':')), value.slice(value.indexOf(':') + 1)]
  if (kind === 'tier' && rest) return { kind, key: rest }
  if (kind === 'preset' && Number.isInteger(Number(rest)) && rest !== '') return { kind, id: Number(rest) }
  return null
}

type StartApplied =
  | { kind: 'tier'; tier: WorkflowTierApplied }
  | { kind: 'preset'; preset: TranslatePresetApplied }

// Saves the chosen starting point on the drama; never starts a run.
export async function applyStart(dramaId: number, value: string): Promise<StartApplied | null> {
  const parsed = parseStartValue(value)
  if (!parsed) return null
  if (parsed.kind === 'tier') return { kind: 'tier', tier: await applyWorkflowTier(dramaId, parsed.key) }
  return { kind: 'preset', preset: await applyTranslatePreset(dramaId, parsed.id) }
}
