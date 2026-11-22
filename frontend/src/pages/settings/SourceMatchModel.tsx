import { useCallback, useEffect, useState } from 'react'

import { getModelDownloads } from '../../api/diagnostics'
import type { DiagnosticsModelDownload } from '../../types/diagnostics'
import { ModelDownload } from '../diagnostics/ModelDownload'

const MODEL_ID = 'bge_m3'

/** Whether the Source match check's model (bge-m3, on the Ollama server) is installed, with the
 * PC-only Download button when it is not. Nothing is shown while the answer is unknown. */
export function SourceMatchModel() {
  const [model, setModel] = useState<DiagnosticsModelDownload | null>(null)
  const load = useCallback(() => {
    getModelDownloads().then((r) => setModel(r.models.find((m) => m.id === MODEL_ID) ?? null), () => setModel(null))
  }, [])
  useEffect(load, [load])

  return model ? <SourceMatchModelView model={model} onFinished={load} /> : null
}

export function SourceMatchModelView({ model, onFinished }: { model: DiagnosticsModelDownload; onFinished: () => void }) {
  return (
    <p className="muted" data-testid="source-match-model">
      {model.downloaded
        ? 'The Source match model (bge-m3) is installed on your Ollama server.'
        : 'The Source match model (bge-m3) is not installed on your Ollama server, so the check does nothing until it is.'}
      {!model.downloaded && <> <ModelDownload model={model} onFinished={onFinished} /></>}
    </p>
  )
}
