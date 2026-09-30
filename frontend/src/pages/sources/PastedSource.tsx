/*
 * PastedSource (SO03): after a verification page, the person finishes it in
 * their own browser, copies the page source and pastes it here. The server
 * reads what is pasted (POST /api/sources/url/preview-pasted, parse only;
 * nothing is sent to the site) and the preview shows as usual; importing
 * the novel text then reads the same paste. The paste is kept in memory for
 * this page only and never displayed back.
 */
import { useState } from 'react'

import { previewPasted } from '../../api/sourcesTools'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import type { PastedPreview } from '../../types/sourcesTools'
import { pastedHtmlProblem } from './sourcesToolsFormat'
import './sources-tools.css'

export function PastedSource({ url, onPreview }: { url: string; onPreview: (html: string, p: PastedPreview) => void }) {
  const [html, setHtml] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const problem = pastedHtmlProblem(html)

  async function go() {
    if (problem || busy) return
    setBusy(true)
    setError(null)
    try {
      onPreview(html, await previewPasted(url, html))
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sources-paste" role="group" aria-label="Continue from pasted page">
      <Field
        label="Finished the check? Paste the page source"
        help="In your browser: right-click the page, View page source, select all and copy. Baihe reads what you paste; it doesn't contact the site for this."
        error={html && problem ? problem : null}
      >
        <textarea
          rows={4}
          value={html}
          spellCheck={false}
          autoComplete="off"
          placeholder="<html>…"
          onChange={(e) => setHtml(e.target.value)}
        />
      </Field>
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={!!problem || busy} aria-busy={busy} onClick={go}>
          {busy ? 'Reading…' : 'Continue from pasted page'}
        </button>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
    </div>
  )
}
