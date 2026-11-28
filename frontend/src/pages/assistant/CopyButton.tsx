// A copy icon for a text block (a proposed patch, a changelog). Reports success or
// failure in words; a blocked clipboard asks the viewer to copy by hand.
import { useState } from 'react'

import { copyText } from '../../components/clipboard'
import { buttonClass } from '../../components/uiClasses'

export function CopyButton({ text, label }: { text: string; label: string }) {
  const [note, setNote] = useState<string | null>(null)

  const copy = async () => {
    setNote((await copyText(text)) ? 'Copied.' : "Couldn't copy. Select the text and copy it by hand.")
  }

  return (
    <span className="assistant-copy">
      <button type="button" className={buttonClass('ghost', 'sm')} aria-label={label} title="Copy" onClick={() => void copy()}>
        <svg className="hb-icon" aria-hidden="true" viewBox="0 0 24 24" width="18" height="18" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="9" y="9" width="12" height="12" rx="2" />
          <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" />
        </svg>
      </button>
      <span className="muted" role="status">
        {note ?? ''}
      </span>
    </span>
  )
}
