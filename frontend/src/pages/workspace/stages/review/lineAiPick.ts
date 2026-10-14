import { useEffect, useMemo, useState } from 'react'

import { withSignal } from '../../../../api/client'
import type { LineAiPick } from '../../../../api/review'
import { getTranslateConfig } from '../../../../api/translateStage'
import { useHolds } from '../../../../hooks/useHolds'
import type { TranslateEngine } from '../../../../types/translate'
import { useEngineGate } from '../useEngineGate'

export interface LinePick {
  engine: string
  model: string
}

// Same engine list as the Translate stage (the run config), so a new engine or
// model shows up here without a second list to keep.
export const NO_PICK: LinePick = { engine: '', model: '' }
const NO_ENGINES: TranslateEngine[] = []
const STORAGE_KEY = 'baihe.review.lineAiPick'
export const NEEDS_PAID_REASON = 'needs paid-engine permission'
export const NO_KEY_REASON = 'no key, add one in Settings'

// A convenience pick for this browser; storage can be blocked, so every access is guarded.
export function loadPick(): LinePick {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null')
    if (raw && typeof raw.engine === 'string' && typeof raw.model === 'string') {
      return { engine: raw.engine, model: raw.model }
    }
  } catch {
    // unreadable storage or a corrupt value: start from the title's default
  }
  return NO_PICK
}

export function savePick(pick: LinePick): void {
  try {
    if (pick.engine) localStorage.setItem(STORAGE_KEY, JSON.stringify(pick))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // the pick just isn't remembered
  }
}

// Why an engine can't be picked, or null.
export function engineBlockReason(engine: TranslateEngine, holdsPaid: boolean): string | null {
  if (!engine.key_configured) return NO_KEY_REASON
  if (!holdsPaid && !engine.free) return NEEDS_PAID_REASON
  return null
}

// An Ollama cloud tag sends the text off this PC, so it is held to engines.paid too.
export function modelBlockReason(engine: TranslateEngine, model: string, holdsPaid: boolean): string | null {
  return !holdsPaid && engine.cloud_models?.includes(model) ? NEEDS_PAID_REASON : null
}

// The remembered pick, minus whatever no longer works (engine removed or without
// a key, permission lost, model dropped from the list): that falls back to the default.
export function usablePick(pick: LinePick, engines: TranslateEngine[], holdsPaid: boolean): LinePick {
  const engine = engines.find((e) => e.name === pick.engine)
  if (!engine || engineBlockReason(engine, holdsPaid)) return NO_PICK
  const listed = engine.models ?? []
  const modelOk = !pick.model
    || (listed.length > 0 ? listed.includes(pick.model) && !modelBlockReason(engine, pick.model, holdsPaid) : true)
  return { engine: engine.name, model: modelOk ? pick.model : '' }
}

export const toApiPick = (pick: LinePick): LineAiPick => ({ engine: pick.engine, model: pick.model })

// Loads the engine list for the title and holds the remembered pick. `ready`
// turns true once the list answered or failed; a failed list leaves the
// default (the server's choice) in force.
export function useLineAiPick(dramaId: number) {
  const holdsPaid = useHolds('engines.paid')
  const [config, setConfig] = useState<{ engines: TranslateEngine[]; defaultEngine: string } | null>(null)
  const [ready, setReady] = useState(false)
  const [stored, setStored] = useState<LinePick>(loadPick)

  useEffect(() => {
    const ctl = new AbortController()
    getTranslateConfig(dramaId, withSignal(ctl.signal))
      .then(
        (c) => !ctl.signal.aborted && setConfig({ engines: c.engines, defaultEngine: c.line_ai_engine ?? c.translation_engine }),
        () => undefined,
      )
      .finally(() => !ctl.signal.aborted && setReady(true))
    return () => ctl.abort()
  }, [dramaId])

  const engines = config?.engines ?? NO_ENGINES
  const pick = useMemo(() => usablePick(stored, engines, holdsPaid), [stored, engines, holdsPaid])
  const setPick = (next: LinePick) => {
    setStored(next)
    savePick(next)
  }
  const defaultEngine = config?.defaultEngine ?? ''
  // Why the engine this request would use can't run (no key, package missing, Ollama stopped), or null.
  const gate = useEngineGate(engines, pick.engine || defaultEngine, undefined)
  return { ready, engines, defaultEngine, holdsPaid, pick, setPick, gate }
}
