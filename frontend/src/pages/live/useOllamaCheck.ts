/** The Live form's Ollama check: installed, and whether it fits in graphics memory, and Start's one "Start anyway?". */
import { useEffect, useState } from 'react'

import { checkOllama } from '../../api/live'

/**
 * `model` is null when the engine is not Ollama, and empty while the model list loads (nothing to check yet); `gpuWhisper` is the Whisper size when Whisper runs on
 * the GPU too. The answer is kept with the check it belongs to, so an old one is never shown for a new model.
 */
export function useOllamaCheck(picked: string | null, gpuWhisper: string | null) {
  const model = picked || null
  const [answer, setAnswer] = useState<{ key: string; note: string | null; gpuFit: boolean } | null>(null)
  const [recheck, setRecheck] = useState(0)
  // A fit warning does not block Start, but a model that does not fit can fail every line,
  // so Start asks once: these hold the check key the question was asked and accepted for.
  const [asked, setAsked] = useState<string | null>(null)
  const [accepted, setAccepted] = useState<string | null>(null)
  const key = `${model}#${gpuWhisper}#${recheck}`
  const note = model !== null && answer?.key === key ? answer.note : null
  const fitWarning = note !== null && answer?.gpuFit === true
  useEffect(() => {
    if (model === null) return
    let alive = true
    checkOllama(model, undefined, gpuWhisper === null ? undefined : { whisperSize: gpuWhisper, useGpu: true }).then(
      (r) => { if (alive) setAnswer({ key, note: r.ok ? null : (r.message ?? 'Ollama is not ready.'), gpuFit: r.gpu_fit === true }) },
      () => {},
    )
    return () => { alive = false }
  }, [model, key, gpuWhisper])
  return {
    note,
    fitWarning,
    asking: fitWarning && asked === key && accepted !== key,
    recheck: () => setRecheck((n) => n + 1),
    /** True when Start should wait for "Start anyway?": a fit warning shows that was not accepted yet. */
    holdStart: () => {
      if (!fitWarning || accepted === key) return false
      setAsked(key)
      return true
    },
    accept: () => setAccepted(key),
  }
}
