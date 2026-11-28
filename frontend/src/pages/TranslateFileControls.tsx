/*
 * The "+ File or screenshot" menu, its "Open a file" field and the "Download" button for the Translate page.
 * Pure logic lives in translateFile.ts; these handlers take their browser
 * pieces (FileReader, object URLs) as arguments so they can be tested in node.
 */
import { buttonClass } from '../components/uiClasses'
import { useDetailsMenu } from '../hooks/useDetailsMenu'
import {
  ACCEPT_ATTR,
  downloadName,
  downloadText,
  fileReaderBytes,
  loadChosenFile,
  type DownloadDeps,
  type FileLoadTarget,
} from './translateFile'

// The native file input is visually hidden; its <label> is drawn as a row of the page's "+ File or screenshot"
// menu (focus shows on the label via translate.css). The file's name or error is FileStatus, outside the menu,
// so it stays in view once the menu closes.
export function OpenFileField({
  id,
  statusId,
  target,
  onChosen,
}: {
  id: string
  statusId: string
  target: FileLoadTarget
  onChosen?: (input: HTMLInputElement) => void
}) {
  return (
    <div className="translate-add-file">
      <input
        id={id}
        type="file"
        className="visually-hidden"
        accept={ACCEPT_ATTR}
        aria-describedby={`${id}-hint ${statusId}`}
        onChange={(e) => {
          const input = e.currentTarget
          onChosen?.(input)
          void loadChosenFile(input.files?.[0], target, fileReaderBytes).finally(() => {
            input.value = ''
          })
        }}
      />
      <label htmlFor={id}>Open a file…</label>
      <span id={`${id}-hint`} className="muted">.txt, .md or .epub; replaces the text</span>
    </div>
  )
}

export function FileOrScreenshotMenu({
  fileId,
  fileStatusId,
  target,
  canRead,
  readOpen,
  onRead,
}: {
  fileId: string
  fileStatusId: string
  target: FileLoadTarget
  canRead: boolean
  readOpen: boolean
  onRead: () => void
}) {
  const menu = useDetailsMenu()
  const close = (from: Element) => {
    const details = from.closest('details')
    if (!details) return
    details.open = false
    // The row that had focus is now hidden; keep keyboard users on the menu button rather than the top of the page.
    details.querySelector('summary')?.focus()
  }
  return (
    <details className="translate-add" ref={menu}>
      <summary className={buttonClass('secondary')}>+ File or screenshot</summary>
      <div className="translate-add-panel">
        <OpenFileField id={fileId} statusId={fileStatusId} target={target} onChosen={close} />
        {canRead && (
          <button
            type="button"
            aria-expanded={readOpen}
            onClick={(e) => {
              onRead()
              close(e.currentTarget)
            }}
          >
            Read from a screenshot…
          </button>
        )}
      </div>
    </details>
  )
}

export function FileStatus({ id, sourceName, message }: { id: string; sourceName: string | null; message: string | null }) {
  if (message) {
    return (
      <span id={id} className="translate-file-error" role="alert">
        {message}
      </span>
    )
  }
  return (
    <span id={id} className="muted" role="status">
      {sourceName ? `Loaded ${sourceName}` : ''}
    </span>
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
