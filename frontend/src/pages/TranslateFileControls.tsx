/*
 * "Open a file" field and "Download result" button for the Translate page.
 * Pure logic lives in translateFile.ts; these handlers take their browser
 * pieces (FileReader, object URLs) as arguments so they can be tested in node.
 */
import { Field } from '../components/Field'
import {
  ACCEPT_ATTR,
  downloadName,
  downloadText,
  fileReaderBytes,
  loadChosenFile,
  type DownloadDeps,
  type FileLoadTarget,
} from './translateFile'

export function OpenFileField({
  sourceName,
  message,
  target,
}: {
  sourceName: string | null
  message: string | null
  target: FileLoadTarget
}) {
  return (
    <div className="translate-file">
      <Field
        label="Open a file"
        help="Plain text (.txt or .md). Its text replaces what is in the box below."
        error={message}
      >
        <input
          type="file"
          accept={ACCEPT_ATTR}
          onChange={(e) => {
            const input = e.currentTarget
            void loadChosenFile(input.files?.[0], target, fileReaderBytes).finally(() => {
              input.value = ''
            })
          }}
        />
      </Field>
      {sourceName && !message && (
        <span className="muted" role="status">
          Loaded {sourceName}
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
      className="translate-download"
      onClick={() => downloadText(result, name, deps)}
    >
      Download result ({name})
    </button>
  )
}
