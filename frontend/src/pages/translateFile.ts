/*
 * Pure helpers for the Translate page's "Open a file" / "Download result".
 * Accepts .txt/.md/.epub, decodes text in the encoding it was saved in, and
 * offers the result as a plain .txt.
 * .epub is unzipped in the browser and its chapter text extracted (see
 * translateEpub.ts); nothing is sent to the server. Size caps: 2 GB of bytes
 * for text files (the largest file we accept), and the text that is read must
 * also fit MAX_TRANSLATE_TEXT_CHARS, the API's limit. An .epub is held and
 * unzipped in memory, so it gets the smaller MAX_EPUB_BYTES cap.
 */
import { MAX_TRANSLATE_TEXT_CHARS } from '../api/translate'
import { EpubError, MAX_EPUB_BYTES, extractEpubText, type HtmlToText } from './translateEpub'

const ACCEPTED_EXTENSIONS = ['.txt', '.md', '.epub'] as const
export const ACCEPT_ATTR = ACCEPTED_EXTENSIONS.join(',')
export const MAX_FILE_BYTES = 2048 * 1024 * 1024

interface FileLike {
  name: string
  size: number
}

/** Reads a file's bytes; the browser default uses FileReader. */
type ReadBytes<F extends FileLike> = (file: F) => Promise<ArrayBuffer>

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
  if (!(ACCEPTED_EXTENSIONS as readonly string[]).includes(ext)) {
    return `"${file.name}" is not a .txt, .md or .epub file.`
  }
  if (ext === '.epub' && file.size > MAX_EPUB_BYTES) {
    return `"${file.name}" is larger than the ${MAX_EPUB_BYTES / 1024 / 1024} MB limit for EPUB files.`
  }
  if (file.size > MAX_FILE_BYTES) return `"${file.name}" is larger than the 2 GB limit.`
  return null
}

// Tried in order when a file is neither UTF-8 nor has a BOM. Chinese web novels
// and spreadsheets saved by Chinese Excel are often GBK, Japanese ones Shift-JIS,
// Korean ones EUC-KR; read as UTF-8 they lose almost every character.
const LEGACY_ORDER: Record<string, string[]> = {
  zh: ['gb18030', 'big5', 'shift_jis', 'euc-kr'],
  ja: ['shift_jis', 'gb18030', 'euc-kr', 'big5'],
  ko: ['euc-kr', 'gb18030', 'shift_jis', 'big5'],
}
const DEFAULT_ORDER = ['gb18030', 'shift_jis', 'euc-kr', 'big5']
const KANA = /[\u3041-\u3096\u30a1-\u30fa]/
// eslint-disable-next-line no-control-regex -- control characters are the sign of a wrong guess
const SUSPECT = /[\ue000-\uf8ff\ufffd\x00-\x08\x0b\x0c\x0e-\x1f]/

function strictDecode(label: string, bytes: Uint8Array): string | null {
  try {
    return new TextDecoder(label, { fatal: true }).decode(bytes)
  } catch {
    return null
  }
}

/**
 * A text file's contents in the encoding it was saved in: a UTF-16 or UTF-8
 * BOM, then UTF-8, then the legacy CJK encodings (the given language's own
 * first). Shift-JIS that reads as kana wins over GB18030, which decodes most
 * Shift-JIS into wrong hanzi (same rule as subtitle_parse.reads_as_shift_jis).
 * Bytes nothing reads are dropped, as before.
 */
export function decodeText(buf: ArrayBuffer, language?: string | null): string {
  const bytes = new Uint8Array(buf)
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le').decode(bytes)
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be').decode(bytes)
  const utf8 = strictDecode('utf-8', bytes)
  if (utf8 !== null) return utf8
  const order = LEGACY_ORDER[language ?? ''] ?? DEFAULT_ORDER
  if (order[0] !== 'shift_jis') {
    const sjis = strictDecode('shift_jis', bytes)
    if (sjis !== null && KANA.test(sjis) && !SUSPECT.test(sjis)) return sjis
  }
  for (const label of order) {
    const text = strictDecode(label, bytes)
    // Private-use or control characters mean the guess was wrong (subtitle_parse does the same);
    // some engines' Big5 maps a stray byte to one instead of failing.
    if (text !== null && !SUSPECT.test(text)) return text
  }
  return new TextDecoder('utf-8', { fatal: false }).decode(bytes).replace(/\uFFFD/g, '')
}

type ReadResult = { ok: true; text: string; name: string } | { ok: false; error: string }

export async function readTranslateFile<F extends FileLike>(
  file: F,
  readBytes: ReadBytes<F>,
  htmlToText?: HtmlToText,
): Promise<ReadResult> {
  const read = await readTranslateFileText(file, readBytes, htmlToText)
  if (read.ok && read.text.length > MAX_TRANSLATE_TEXT_CHARS) {
    const limit = MAX_TRANSLATE_TEXT_CHARS.toLocaleString('en-US')
    return { ok: false, error: `"${file.name}" has more than the ${limit} characters that can be translated at once.` }
  }
  return read
}

async function readTranslateFileText<F extends FileLike>(
  file: F,
  readBytes: ReadBytes<F>,
  htmlToText?: HtmlToText,
): Promise<ReadResult> {
  const invalid = checkTranslateFile(file)
  if (invalid) return { ok: false, error: invalid }
  let buf: ArrayBuffer
  try {
    buf = await readBytes(file)
  } catch {
    return { ok: false, error: `Could not read "${file.name}".` }
  }
  if (extensionOf(file.name) !== '.epub') return { ok: true, text: decodeText(buf), name: file.name }
  try {
    return { ok: true, text: extractEpubText(new Uint8Array(buf), file.name, htmlToText), name: file.name }
  } catch (e) {
    const error = e instanceof EpubError ? e.message : `Could not read the text in "${file.name}".`
    return { ok: false, error }
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
  htmlToText?: HtmlToText,
): Promise<void> {
  if (!file) return
  const read = await readTranslateFile(file, readBytes, htmlToText)
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
    // Revoke after the click task, not synchronously, so the browser can start the download first.
    setTimeout(() => deps.revokeObjectURL(url), 0)
  }
}
