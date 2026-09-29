/*
 * Pure helpers for the Translate page's "Open a file" / "Download result".
 * Parity with tabs/translate_tab.py: it accepted .txt/.md/.epub, decoded text
 * as UTF-8 ignoring bad bytes, and offered the result as a plain .txt.
 * .epub needs an unzip library on the client, so it is refused with a clear
 * message instead (no new dependency). Size cap: the API has no text limit;
 * Streamlit's own upload cap (.streamlit/config.toml maxUploadSize = 2048 MB)
 * is the only limit the old tab enforced, so it is reused here.
 */

export const ACCEPTED_EXTENSIONS = ['.txt', '.md'] as const
export const ACCEPT_ATTR = ACCEPTED_EXTENSIONS.join(',')
export const MAX_FILE_BYTES = 2048 * 1024 * 1024

export interface FileLike {
  name: string
  size: number
}

/** Reads a file's bytes; the browser default uses FileReader. */
export type ReadBytes<F extends FileLike> = (file: F) => Promise<ArrayBuffer>

export function fileReaderBytes(file: Blob): Promise<ArrayBuffer> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as ArrayBuffer)
    reader.onerror = () => reject(reader.error)
    reader.readAsArrayBuffer(file)
  })
}

export function extensionOf(name: string): string {
  const i = name.lastIndexOf('.')
  return i <= 0 ? '' : name.slice(i).toLowerCase()
}

/** Returns an error message, or null if the file can be opened. */
export function checkTranslateFile(file: Pick<FileLike, 'name' | 'size'>): string | null {
  const ext = extensionOf(file.name)
  if (ext === '.epub') {
    return 'EPUB files cannot be opened here yet. Save the book as .txt and open that instead.'
  }
  if (!(ACCEPTED_EXTENSIONS as readonly string[]).includes(ext)) {
    return `"${file.name}" is not a .txt or .md file.`
  }
  if (file.size > MAX_FILE_BYTES) return `"${file.name}" is larger than the 2 GB limit.`
  return null
}

/** Decode like the old tab: UTF-8, invalid bytes dropped (not replaced). */
export function decodeText(buf: ArrayBuffer): string {
  return new TextDecoder('utf-8', { fatal: false }).decode(buf).replace(/\uFFFD/g, '')
}

export type ReadResult = { ok: true; text: string; name: string } | { ok: false; error: string }

export async function readTranslateFile<F extends FileLike>(
  file: F,
  readBytes: ReadBytes<F>,
): Promise<ReadResult> {
  const invalid = checkTranslateFile(file)
  if (invalid) return { ok: false, error: invalid }
  try {
    return { ok: true, text: decodeText(await readBytes(file)), name: file.name }
  } catch {
    return { ok: false, error: `Could not read "${file.name}".` }
  }
}

export interface FileLoadTarget {
  setText(text: string): void
  setSourceName(name: string | null): void
  setFileMessage(message: string | null): void
}

/** Reads the chosen file and fills the text box, or shows why it could not. */
export async function loadChosenFile<F extends FileLike>(
  file: F | undefined,
  target: FileLoadTarget,
  readBytes: ReadBytes<F>,
): Promise<void> {
  if (!file) return
  const read = await readTranslateFile(file, readBytes)
  if (!read.ok) {
    target.setFileMessage(read.error)
    return
  }
  target.setFileMessage(null)
  target.setSourceName(read.name)
  target.setText(read.text)
}

/** "chapter1.md" + "en" -> "chapter1.en.txt"; no source -> "translation.en.txt". */
export function downloadName(sourceName: string | null, targetLanguage: string): string {
  const base = (sourceName ?? '').replace(/[\\/]/g, '_')
  const dot = base.lastIndexOf('.')
  const stem = (dot > 0 ? base.slice(0, dot) : base).trim() || 'translation'
  const lang = targetLanguage.replace(/[^a-zA-Z-]/g, '') || 'out'
  return `${stem}.${lang}.txt`
}

export interface DownloadDeps {
  createObjectURL(b: Blob): string
  revokeObjectURL(u: string): void
  click(href: string, filename: string): void
}

function browserDeps(): DownloadDeps {
  return {
    createObjectURL: (b) => URL.createObjectURL(b),
    revokeObjectURL: (u) => URL.revokeObjectURL(u),
    click: (href, filename) => {
      const a = document.createElement('a')
      a.href = href
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
    },
  }
}

/** Save text as a UTF-8 .txt via Blob + object URL. */
export function downloadText(text: string, filename: string, deps: DownloadDeps = browserDeps()) {
  const url = deps.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
  try {
    deps.click(url, filename)
  } finally {
    setTimeout(() => deps.revokeObjectURL(url), 0)
  }
}
