import { useEffect, useMemo, useState } from 'react'

// Generated text shown in full, with Copy and a real download link
// (Blob URL + the anchor's download attribute).
export function ExportTextResult({ text, filename, mime }: { text: string; filename: string; mime: string }) {
  const [copiedText, setCopiedText] = useState<string | null>(null)
  const url = useMemo(() => URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` })), [text, mime])
  useEffect(() => () => URL.revokeObjectURL(url), [url])
  const copied = copiedText === text

  if (!text.trim()) {
    return <p className="muted" data-testid="export-empty">Nothing to export yet: this drama has no lines.</p>
  }
  return (
    <div className="export-result">
      <div className="export-actions">
        <button
          type="button"
          onClick={() => navigator.clipboard.writeText(text).then(() => setCopiedText(text), () => setCopiedText(null))}
        >
          Copy
        </button>
        <a href={url} download={filename} data-testid="export-download">
          Download {filename}
        </a>
        {copied && <span role="status">Copied.</span>}
      </div>
      <pre className="export-text" data-testid="export-text" tabIndex={0}>{text}</pre>
    </div>
  )
}
