/** The Live form's Ollama check: whether Ollama answers, so a hosted model can be reached. */
import { useEffect, useState } from 'react'

import { checkOllama } from '../../api/live'

/**
 * `model` is null when the engine is not Ollama, and empty while the model list loads (nothing to check yet).
 * The answer is kept with the check it belongs to, so an old one is never shown for a new model.
 */
export function useOllamaCheck(picked: string | null) {
  const model = picked || null
  const [answer, setAnswer] = useState<{ key: string; note: string | null } | null>(null)
  const [recheck, setRecheck] = useState(0)
  const key = `${model}#${recheck}`
  const note = model !== null && answer?.key === key ? answer.note : null
  useEffect(() => {
    if (model === null) return
    let alive = true
    checkOllama(model).then(
      (r) => { if (alive) setAnswer({ key, note: r.ok ? null : (r.message ?? 'Ollama is not ready.') }) },
      () => {},
    )
    return () => { alive = false }
  }, [model, key])
  return { note, recheck: () => setRecheck((n) => n + 1) }
}
