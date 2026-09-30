/*
 * PastedListing (DI07 manual fallback): some sites build their listings
 * with JavaScript, so a plain fetch gets only page furniture. Open the
 * listing in your browser, select all, copy, paste it here. The text goes
 * through the same AI extraction and the same review and "Add to catalogue"
 * (POST /api/discover/bulk-extract/pasted runs the bulk-extract job). The
 * pasted text is never shown back.
 */
import { useState } from 'react'

import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { pastedListingProblem } from '../sources/sourcesToolsFormat'

type Props = {
  aiReady: boolean
  running: boolean
  // Open it: the last run found a page that needs the manual paste.
  suggested: boolean
  onExtract: (text: string) => void
}

export function PastedListing({ aiReady, running, suggested, onExtract }: Props) {
  const [text, setText] = useState('')
  const problem = pastedListingProblem(text)
  return (
    <Section
      key={suggested ? 'suggested' : 'plain'}
      title="Paste the listing text instead"
      summary="For listings a plain fetch can't read"
      defaultOpen={suggested}
    >
      <form
        className="discover-block"
        onSubmit={(e) => {
          e.preventDefault()
          if (!problem && aiReady && !running) onExtract(text)
        }}
      >
        <p className="muted">
          Open the listing in your browser, select all (Ctrl+A), copy and paste it here. Only the first 12,000 characters
          are read.
        </p>
        <Field label="Pasted listing text" error={text && problem ? problem : null}>
          <textarea rows={5} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        {!aiReady && <p className="muted">Still needed: an AI engine (set a key in Settings).</p>}
        <div className="discover-row">
          <button type="submit" className={buttonClass('secondary')} disabled={!!problem || !aiReady || running}>
            {running ? 'Extracting…' : 'Extract from pasted text'}
          </button>
        </div>
      </form>
    </Section>
  )
}
