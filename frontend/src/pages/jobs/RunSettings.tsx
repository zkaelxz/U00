import { useEffect, useState } from 'react'

import { withSignal } from '../../api/client'
import { getAsrAdvice } from '../../api/workspace'
import type { AsrAdviceNote, AsrAdviceRequest } from '../../types/workspace'
import { adviceRequestFromRun, engineThatRan, runSettingRows } from './runSettingsData'

/** The warnings that applied to the combination a finished run used; nothing when none did. */
function RunAdvice({ request }: { request: AsrAdviceRequest }) {
  const key = JSON.stringify(request)
  const [notes, setNotes] = useState<AsrAdviceNote[]>([])
  useEffect(() => {
    const ctl = new AbortController()
    getAsrAdvice(JSON.parse(key) as AsrAdviceRequest, withSignal(ctl.signal))
      .then((all) => setNotes(all.filter((n) => n.severity !== 'tip')))
      .catch(() => setNotes([]))
    return () => ctl.abort()
  }, [key])
  if (notes.length === 0) return null
  return (
    <ul className="muted" aria-label="Notes on these settings" data-testid="run-advice">
      {notes.map((n) => <li key={n.id}>{n.text}</li>)}
    </ul>
  )
}

/** "Run settings" for one job's Details panel; renders nothing without any. */
export function RunSettings({ result }: { result: Record<string, unknown> | null | undefined }) {
  const rows = runSettingRows(result)
  if (rows.length === 0) return null
  const engine = engineThatRan(result)
  const request = adviceRequestFromRun(result)
  return (
    <section aria-label="Run settings">
      <h2 className="stage-times-title subhead">Run settings</h2>
      {engine && <p data-testid="engine-that-ran">Engine that ran: {engine}</p>}
      {request && <RunAdvice request={request} />}
      <dl>
        {rows.map(([label, value]) => (
          <span key={label} className="jobs-field"><dt>{label}</dt><dd>{value}</dd></span>
        ))}
      </dl>
    </section>
  )
}
