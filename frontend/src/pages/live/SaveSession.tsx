import { useState } from 'react'

import { downloadText } from '../../components/downloadBlob'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import type { LiveCue } from '../../types/live'
import { toSrt, toText, type SaveContent } from './saveSession'

/** Saves every line the page has received, not only the newest ones the feed shows. */
export function SaveSession({ cues }: { cues: LiveCue[] }) {
  const [content, setContent] = useState<SaveContent>('both')
  const stamp = new Date().toISOString().slice(0, 10)
  const save = (kind: 'srt' | 'txt') => {
    const body = kind === 'srt' ? toSrt(cues, content) : toText(cues, content)
    if (body) downloadText(body, `live-session-${stamp}.${kind}`, kind === 'srt' ? 'application/x-subrip' : 'text/plain')
  }
  return (
    <div className="live-save" data-testid="live-save">
      <Field label="Save">
        <select value={content} onChange={(e) => setContent(e.target.value as SaveContent)}>
          <option value="both">Translation and original</option>
          <option value="translation">Translation only</option>
          <option value="original">Original only</option>
        </select>
      </Field>
      <button type="button" className={buttonClass('secondary')} onClick={() => save('srt')}>Save as SRT</button>
      <button type="button" className={buttonClass('secondary')} onClick={() => save('txt')}>Save as text</button>
    </div>
  )
}
