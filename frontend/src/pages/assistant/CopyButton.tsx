// "Copy" for a text block (a proposed patch, a changelog). Reports success or
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
      <button type="button" className={buttonClass('secondary', 'sm')} aria-label={label} onClick={() => void copy()}>
        Copy
      </button>
      <span className="muted" role="status">
        {note ?? ''}
      </span>
    </span>
  )
}
