import { useEffect, useMemo, useRef, useState } from 'react'

import { copyText } from '../../../components/clipboard'

// Generated text shown in full, with Copy and a real download link
// (Blob URL + the anchor's download attribute).
export function ExportTextResult({ text, filename, mime }: { text: string; filename: string; mime: string }) {
  const [copied, setCopied] = useState<{ text: string; ok: boolean } | null>(null)
  const preRef = useRef<HTMLPreElement>(null)
  const url = useMemo(() => URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` })), [text, mime])
  useEffect(() => () => URL.revokeObjectURL(url), [url])
  const status = copied?.text === text ? copied.ok : null

  const copy = async () => {
    const ok = await copyText(text)
    setCopied({ text, ok })
    if (!ok && preRef.current) {
      // Leave the text selected so the user can copy it by hand.
      preRef.current.focus()
      window.getSelection()?.selectAllChildren(preRef.current)
    }
  }

  if (!text.trim()) {
    return <p className="muted" data-testid="export-empty">Nothing to export yet: this title has no lines.</p>
  }
  return (
    <div className="export-result">
      <div className="export-actions">
        <button type="button" onClick={() => void copy()}>
          Copy
        </button>
        <a href={url} download={filename} data-testid="export-download">
          Download {filename}
        </a>
        {status !== null && (
          <span role="status">{status ? 'Copied.' : "Couldn't copy automatically. The text below is selected: press Ctrl+C, or long-press on a phone."}</span>
        )}
      </div>
      <pre ref={preRef} className="export-text" data-testid="export-text" tabIndex={0}>{text}</pre>
    </div>
  )
}
