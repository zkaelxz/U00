/*
 * Client-side .epub -> plain text for the Translate page's "Open a file".
 * Parity with tabs/translate_tab.py, which called core.load_novel_text_for_context
 * -> epub_io.import_epub_text: every chapter's text, script/style dropped,
 * chapters joined by a blank line. Differences, on purpose: chapters follow the
 * OPF spine (reading order) rather than manifest order, only each chapter's
 * <body> is read (no <title> repeats), and each paragraph becomes one line
 * instead of get_text(separator="\n")'s one-line-per-text-node.
 *
 * Safety: the book is only unzipped (fflate) and parsed, never rendered.
 * container.xml and the OPF are read with small attribute scanners; chapter
 * XHTML goes through DOMParser, whose documents are inert (no scripts run, no
 * images load) and are never attached to the page.
 *
 * Zip-bomb caps. The zip's central directory is read here and only text entries
 * (.xml/.opf/.xhtml/...) are ever unpacked; images and fonts are never touched.
 * Declared sizes are never trusted: a deflate stream decodes to its real size
 * whatever the header claims (fflate keeps decoding past a too-small output
 * buffer), and several directory records may point at the same data. So each
 * text entry is fed to fflate's streaming Inflate in small chunks, and unpacking
 * stops as soon as the real output, summed over all text entries, passes the
 * cap; the stored bytes read are capped the same way (a stream of empty blocks
 * costs CPU with no output). One chunk can add at most ~1032x its size before
 * the check runs, so a bomb overshoots the cap by at most ~17 MB.
 */
import { Inflate } from 'fflate'

export const MAX_EPUB_BYTES = 200 * 1024 * 1024
export const MAX_EPUB_ENTRIES = 10_000
export const MAX_EPUB_UNPACKED_BYTES = 100 * 1024 * 1024

export interface EpubLimits {
  maxEntries: number
  maxUnpackedBytes: number
}

const DEFAULT_LIMITS: EpubLimits = { maxEntries: MAX_EPUB_ENTRIES, maxUnpackedBytes: MAX_EPUB_UNPACKED_BYTES }

/** A problem with the book, worded for the person who chose it. */
export class EpubError extends Error {}

/** The slice of the DOM Node interface the text walker needs. */
export interface TextNode {
  nodeType: number
  nodeName: string
  nodeValue: string | null
  childNodes: ArrayLike<TextNode>
}

/** Turns one chapter's XHTML into text; the browser default uses DOMParser. */
export type HtmlToText = (markup: string) => string

const ELEMENT_NODE = 1
const TEXT_NODE = 3
const DOCUMENT_NODE = 9
const SKIP = new Set(['script', 'style', 'template', 'noscript', 'head'])
const BLOCK = new Set([
  'address', 'article', 'aside', 'blockquote', 'br', 'dd', 'div', 'dl', 'dt',
  'figcaption', 'figure', 'footer', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'header',
  'hr', 'li', 'main', 'nav', 'ol', 'p', 'pre', 'section', 'table', 'td', 'th', 'tr', 'ul',
])

/** Text of a node tree, one line per block element, blank lines dropped. */
export function nodeText(root: TextNode): string {
  const parts: string[] = []
  const walk = (node: TextNode) => {
    if (node.nodeType === TEXT_NODE) {
      parts.push((node.nodeValue ?? '').replace(/\s+/g, ' '))
      return
    }
    if (node.nodeType !== ELEMENT_NODE && node.nodeType !== DOCUMENT_NODE) return
    const name = node.nodeName.toLowerCase().replace(/^.*:/, '')
    if (SKIP.has(name)) return
    const block = BLOCK.has(name)
    if (block) parts.push('\n')
    for (let i = 0; i < node.childNodes.length; i++) walk(node.childNodes[i])
    if (block) parts.push('\n')
  }
  walk(root)
  return parts
    .join('')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .join('\n')
}

/**
 * Parse as XHTML first, so self-closed tags like <title/> or <script src=""/>
 * close (as BeautifulSoup did for the Streamlit tab); fall back to the lenient
 * HTML parser when the chapter is not well-formed XML.
 */
export function domHtmlToText(markup: string): string {
  const parser = new DOMParser()
  let doc = parser.parseFromString(markup, 'application/xhtml+xml')
  if (doc.getElementsByTagName('parsererror').length > 0) doc = parser.parseFromString(markup, 'text/html')
  return nodeText(doc.body ?? doc.documentElement)
}

const ENTITIES: Record<string, string> = { amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" }

function decodeEntities(s: string): string {
  return s.replace(/&(#x[0-9a-f]+|#\d+|\w+);/gi, (m, e: string) => {
    if (e[0] === '#') {
      const code = e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10)
      return Number.isFinite(code) && code <= 0x10ffff ? String.fromCodePoint(code) : m
    }
    return ENTITIES[e] ?? m
  })
}

/** Attributes of every <tag ...> (any namespace prefix) in an XML string. */
export function xmlTags(xml: string, tag: string): Record<string, string>[] {
  const clean = xml.replace(/<!--[\s\S]*?-->/g, '').replace(/<!\[CDATA\[[\s\S]*?\]\]>/g, '')
  const tagRe = new RegExp(`<(?:[\\w.-]+:)?${tag}(?=[\\s/>])([^>]*)>`, 'g')
  const out: Record<string, string>[] = []
  for (const m of clean.matchAll(tagRe)) {
    const attrs: Record<string, string> = {}
    for (const a of m[1].matchAll(/([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) {
      attrs[a[1].replace(/^.*:/, '')] = decodeEntities(a[2] ?? a[3] ?? '')
    }
    out.push(attrs)
  }
  return out
}

/** Resolve an OPF-relative href to a zip entry path ("OEBPS/a/../b.xhtml" -> "OEBPS/b.xhtml"). */
export function resolveHref(baseDir: string, href: string): string {
  let path = href.split('#')[0]
  try {
    path = decodeURIComponent(path)
  } catch {
    // keep the raw href if it is not valid percent-encoding
  }
  const segs: string[] = []
  for (const s of (path.startsWith('/') ? path : `${baseDir}/${path}`).split('/')) {
    if (s === '' || s === '.') continue
    if (s === '..') segs.pop()
    else segs.push(s)
  }
  return segs.join('/')
}

const WANTED = /(?:\.(?:xml|opf|xhtml|html|htm|xht))$/i

const INFLATE_CHUNK = 16 * 1024

interface ZipEntry {
  name: string
  method: number
  data: Uint8Array
}

/** Zip entries from the central directory (zip64 aware); throws on a malformed layout. */
function readZipEntries(b: Uint8Array, maxEntries: number, tooMany: () => Error): ZipEntry[] {
  const v = new DataView(b.buffer, b.byteOffset, b.byteLength)
  const u16 = (o: number) => v.getUint16(o, true)
  const u32 = (o: number) => v.getUint32(o, true)
  const u64 = (o: number) => Number(v.getBigUint64(o, true))
  let eocd = b.length - 22
  while (eocd >= 0 && u32(eocd) !== 0x06054b50) {
    if (b.length - eocd > 22 + 0xffff) throw new Error('no end of central directory')
    eocd--
  }
  if (eocd < 0) throw new Error('no end of central directory')
  let count = u16(eocd + 10)
  let offset = u32(eocd + 16)
  if ((count === 0xffff || offset === 0xffffffff) && eocd >= 20 && u32(eocd - 20) === 0x07064b50) {
    const z = u64(eocd - 12)
    if (u32(z) !== 0x06064b50) throw new Error('bad zip64 end record')
    count = u64(z + 32)
    offset = u64(z + 48)
  }
  if (count > maxEntries) throw tooMany()
  const entries: ZipEntry[] = []
  for (let i = 0, p = offset; i < count; i++) {
    if (u32(p) !== 0x02014b50) throw new Error('bad central directory record')
    const flags = u16(p + 8)
    const method = u16(p + 10)
    let size = u32(p + 20)
    const originalSize = u32(p + 24)
    const nameLen = u16(p + 28)
    const extraLen = u16(p + 30)
    let local = u32(p + 42)
    const name = new TextDecoder(flags & 0x800 ? 'utf-8' : 'latin1').decode(b.subarray(p + 46, p + 46 + nameLen))
    // zip64: the extra field holds, in order, whichever of these were 0xFFFFFFFF.
    for (let e = p + 46 + nameLen, end = e + extraLen; e + 4 <= end; e += 4 + u16(e + 2)) {
      if (u16(e) !== 1) continue
      let f = e + 4
      if (originalSize === 0xffffffff) f += 8
      if (size === 0xffffffff) {
        size = u64(f)
        f += 8
      }
      if (local === 0xffffffff) local = u64(f)
      break
    }
    if (u32(local) !== 0x04034b50) throw new Error('bad local header')
    const start = local + 30 + u16(local + 26) + u16(local + 28)
    if (start + size > b.length) throw new Error('entry runs past the end')
    entries.push({ name, method, data: b.subarray(start, start + size) })
    p += 46 + nameLen + extraLen + u16(p + 32)
  }
  return entries
}

function unzipEpub(bytes: Uint8Array, name: string, limits: EpubLimits): Record<string, Uint8Array> {
  const tooMany = () =>
    new EpubError(`"${name}" has more than ${limits.maxEntries.toLocaleString('en')} files inside, too many to open safely.`)
  const tooBig = () => {
    const mb = Math.round((limits.maxUnpackedBytes / 1024 / 1024) * 100) / 100
    return new EpubError(`"${name}" unpacks to more than ${mb} MB of text, too much to open safely.`)
  }
  const bad = () => new EpubError(`"${name}" is not a readable EPUB (it could not be unzipped).`)
  let entries: ZipEntry[]
  try {
    entries = readZipEntries(bytes, limits.maxEntries, tooMany)
  } catch (e) {
    throw e instanceof EpubError ? e : bad()
  }
  let readLeft = limits.maxUnpackedBytes
  let outLeft = limits.maxUnpackedBytes
  const files: Record<string, Uint8Array> = {}
  for (const entry of entries) {
    if (!WANTED.test(entry.name)) continue
    readLeft -= entry.data.length
    if (readLeft < 0) throw tooBig()
    if (entry.method === 0) {
      outLeft -= entry.data.length
      if (outLeft < 0) throw tooBig()
      files[entry.name] = entry.data
      continue
    }
    if (entry.method !== 8) throw bad()
    const chunks: Uint8Array[] = []
    const inflate = new Inflate((chunk) => {
      outLeft -= chunk.length
      chunks.push(chunk)
    })
    try {
      for (let at = 0; ; at += INFLATE_CHUNK) {
        const end = Math.min(at + INFLATE_CHUNK, entry.data.length)
        inflate.push(entry.data.subarray(at, end), end === entry.data.length)
        if (outLeft < 0) throw tooBig()
        if (end === entry.data.length) break
      }
    } catch (e) {
      throw e instanceof EpubError ? e : bad()
    }
    const out = new Uint8Array(chunks.reduce((n, c) => n + c.length, 0))
    let at = 0
    for (const c of chunks) {
      out.set(c, at)
      at += c.length
    }
    files[entry.name] = out
  }
  return files
}

/**
 * Decode an XML/XHTML file in the encoding its <?xml ... encoding="..."?>
 * declaration names (e.g. gbk), UTF-8 otherwise; bad bytes are dropped like
 * the text-file path does.
 */
export function decodeXml(b: Uint8Array): string {
  const head = new TextDecoder('latin1').decode(b.subarray(0, 200))
  const declared = /^\s*<\?xml[^>]*\bencoding\s*=\s*["']([\w.:-]+)["']/i.exec(head)?.[1]
  let decoder = new TextDecoder('utf-8')
  // A declaration readable as ASCII can't be UTF-16 (XML spec, Appendix F).
  if (declared && !/^utf-?16/i.test(declared)) {
    try {
      decoder = new TextDecoder(declared)
    } catch {
      // unknown label: keep UTF-8
    }
  }
  return decoder.decode(b).replace(/\uFFFD/g, '')
}

/**
 * Extract a book's chapter text in spine order, chapters separated by a blank
 * line. Throws EpubError with a plain-English message for anything it can't read.
 */
export function extractEpubText(
  bytes: Uint8Array,
  name: string,
  htmlToText: HtmlToText = domHtmlToText,
  limits: EpubLimits = DEFAULT_LIMITS,
): string {
  const files = unzipEpub(bytes, name, limits)
  const bad = (why: string) => new EpubError(`"${name}" is not a readable EPUB (${why}).`)

  const container = files['META-INF/container.xml']
  if (!container) throw bad('META-INF/container.xml is missing')
  const opfPath = xmlTags(decodeXml(container), 'rootfile').find((r) => r['full-path'])?.['full-path']
  if (!opfPath) throw bad('container.xml names no package file')
  const opf = files[resolveHref('', opfPath)]
  if (!opf) throw bad(`its package file ${opfPath} is missing`)

  const opfXml = decodeXml(opf)
  const opfDir = resolveHref('', opfPath).split('/').slice(0, -1).join('/')
  const manifest = new Map<string, { href: string; type: string }>()
  for (const item of xmlTags(opfXml, 'item')) {
    if (item.id && item.href) manifest.set(item.id, { href: item.href, type: item['media-type'] ?? '' })
  }
  const chapters = [
    ...new Set(
      xmlTags(opfXml, 'itemref')
        .map((ref) => manifest.get(ref.idref ?? ''))
        .filter((it): it is { href: string; type: string } => !!it && /html/i.test(it.type))
        .map((it) => resolveHref(opfDir, it.href)),
    ),
  ]
  if (chapters.length === 0) throw new EpubError(`"${name}" has no chapters to read.`)

  const encryption = files['META-INF/encryption.xml']
  if (encryption) {
    const locked = new Set(xmlTags(decodeXml(encryption), 'CipherReference').map((c) => resolveHref('', c.URI ?? '')))
    if (chapters.some((c) => locked.has(c))) {
      throw new EpubError(`"${name}" is copy-protected (DRM), so its text cannot be read.`)
    }
  }

  const texts: string[] = []
  for (const path of chapters) {
    const doc = files[path]
    if (!doc) continue
    const text = htmlToText(decodeXml(doc)).trim()
    if (text) texts.push(text)
  }
  if (texts.length === 0) throw new EpubError(`"${name}" has no text in its chapters.`)
  return texts.join('\n\n')
}
