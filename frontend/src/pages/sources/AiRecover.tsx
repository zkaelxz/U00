/*
 * AiRecover: chapters an import stopped on because the source's page layout
 * changed ("Needs AI help"). Nothing runs until the person picks an engine
 * and presses Confirm, which shows the engine, "1 AI call" and whether it
 * may be paid. The job ends in a Review extraction; nothing is saved before
 * that is imported.
 *
 *   <AiRecover rows={needsAiRows(state)} disabled={running} onConfirm={(id, engine) => ...} />
 */
import { useState } from 'react'

import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import type { ImportRetryRow } from '../../types/sourcesImport'
import { engineLabel, recoverSummary } from './extractionFormat'
import { useAiEngines } from './useAiEngines'
import { AI_ENGINE_LABEL } from '../../helpText'

type Props = {
  rows: ImportRetryRow[]
  disabled: boolean
  onConfirm: (chapterId: string, engine: string) => void
}

export function AiRecover({ rows, disabled, onConfirm }: Props) {
  const ai = useAiEngines(rows.length > 0)
  const [asking, setAsking] = useState<string | null>(null)
  const [picked, setPicked] = useState<string | null>(null)
  if (rows.length === 0) return null
  const engine = picked && ai.engines?.engines.includes(picked) ? picked : (ai.engines?.default ?? null)
  return (
    <div className="sources-ai-recover" role="group" aria-label="Needs AI help" data-testid="ai-recover">
      <ErrorBanner error={ai.error} />
      <ul className="sources-outcome-list">
        {rows.map((r) => (
          <li key={r.chapter_id}>
            <span>{r.title || r.chapter_id}</span>
            <span className="warn">Needs AI help</span>
            {asking !== r.chapter_id ? (
              <button type="button" className={buttonClass('secondary')} disabled={disabled} onClick={() => setAsking(r.chapter_id)}>
                Use AI help…
              </button>
            ) : (
              <div className="sources-ai-confirm">
                <p className="muted">{r.error}</p>
                <Field label={AI_ENGINE_LABEL}>
                  <select value={engine ?? ''} disabled={disabled} onChange={(e) => setPicked(e.target.value || null)}>
                    {!engine && <option value="">Choose an engine…</option>}
                    {(ai.engines?.engines ?? []).map((n) => (
                      <option key={n} value={n}>
                        {engineLabel(n)}
                      </option>
                    ))}
                  </select>
                </Field>
                {engine && <p data-testid="ai-recover-summary">{recoverSummary(engine, ai.engines)}</p>}
                <div className="actions">
                  <button
                    type="button"
                    className={buttonClass('primary')}
                    disabled={disabled || !engine}
                    onClick={() => {
                      if (!engine) return
                      setAsking(null)
                      onConfirm(r.chapter_id, engine)
                    }}
                  >
                    Confirm
                  </button>
                  <button type="button" className={buttonClass('ghost')} onClick={() => setAsking(null)}>
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
