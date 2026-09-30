/*
 * "Open a file" field and "Download" button for the Translate page.
 * Pure logic lives in translateFile.ts; these handlers take their browser
 * pieces (FileReader, object URLs) as arguments so they can be tested in node.
 */
import { useId } from 'react'

import { buttonClass } from '../components/uiClasses'
import {
  ACCEPT_ATTR,
  downloadName,
  downloadText,
  fileReaderBytes,
  loadChosenFile,
  type DownloadDeps,
  type FileLoadTarget,
} from './translateFile'

// The native file input is visually hidden; its <label> is drawn as a small
// secondary button (focus shows on the label via translate.css). The hint and
// the result line stay visible text, never a tooltip.
export function OpenFileField({
  sourceName,
  message,
  target,
}: {
  sourceName: string | null
  message: string | null
  target: FileLoadTarget
}) {
  const id = useId()
  const hintId = `${id}-hint`
  return (
    <div className="translate-file">
      <input
        id={id}
        type="file"
        className="visually-hidden"
        accept={ACCEPT_ATTR}
        aria-describedby={hintId}
        onChange={(e) => {
          const input = e.currentTarget
          void loadChosenFile(input.files?.[0], target, fileReaderBytes).finally(() => {
            input.value = ''
          })
        }}
      />
      <label htmlFor={id} className={buttonClass('secondary', 'sm')}>
        Open a file…
      </label>
      {message ? (
        <span id={hintId} className="translate-file-error" role="alert">
          {message}
        </span>
      ) : sourceName ? (
        <span id={hintId} className="muted" role="status">
          Loaded {sourceName}
        </span>
      ) : (
        <span id={hintId} className="muted">
          .txt, .md or .epub; replaces the text below
        </span>
      )}
    </div>
  )
}

export function DownloadResultButton({
  result,
  sourceName,
  targetLanguage,
  deps,
}: {
  result: string
  sourceName: string | null
  targetLanguage: string
  deps?: DownloadDeps
}) {
  const name = downloadName(sourceName, targetLanguage)
  return (
    <button
      type="button"
      className={buttonClass('secondary', 'sm', 'translate-download')}
      aria-label={`Download result (${name})`}
      onClick={() => downloadText(result, name, deps)}
    >
      Download
    </button>
  )
}
