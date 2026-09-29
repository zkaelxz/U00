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
 * images load) and are never attached to the page. Zip-bomb caps: entry count
 * and the total declared unpacked size of the entries we inflate (fflate
 * inflates into a buffer of exactly the declared size, so a lying header
 * cannot make it grow).
 */
import { unzipSync, type UnzipFileInfo } from 'fflate'

export const MAX_EPUB_BYTES = 200 * 1024 * 1024
export const MAX_EPUB_ENTRIES = 10_000
export const MAX_EPUB_UNPACKED_BYTES = 100 * 1024 * 1024

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

export function domHtmlToText(markup: string): string {
  const doc = new DOMParser().parseFromString(markup, 'text/html')
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

function unzipEpub(bytes: Uint8Array, name: string): Record<string, Uint8Array> {
  let entries = 0
  let unpacked = 0
  try {
    return unzipSync(bytes, {
      filter: (f: UnzipFileInfo) => {
        entries += 1
        if (entries > MAX_EPUB_ENTRIES) {
          throw new EpubError(
            `"${name}" has more than ${MAX_EPUB_ENTRIES.toLocaleString('en')} files inside, too many to open safely.`,
          )
        }
        if (!WANTED.test(f.name)) return false
        unpacked += f.originalSize
        if (unpacked > MAX_EPUB_UNPACKED_BYTES) {
          throw new EpubError(
            `"${name}" unpacks to more than ${MAX_EPUB_UNPACKED_BYTES / 1024 / 1024} MB of text, too much to open safely.`,
          )
        }
        return true
      },
    })
  } catch (e) {
    if (e instanceof EpubError) throw e
    throw new EpubError(`"${name}" is not a readable EPUB (it could not be unzipped).`)
  }
}

const utf8 = (b: Uint8Array) => new TextDecoder('utf-8', { fatal: false }).decode(b).replace(/�/g, '')

/**
 * Extract a book's chapter text in spine order, chapters separated by a blank
 * line. Throws EpubError with a plain-English message for anything it can't read.
 */
export function extractEpubText(
  bytes: Uint8Array,
  name: string,
  htmlToText: HtmlToText = domHtmlToText,
): string {
  const files = unzipEpub(bytes, name)
  const bad = (why: string) => new EpubError(`"${name}" is not a readable EPUB (${why}).`)

  const container = files['META-INF/container.xml']
  if (!container) throw bad('META-INF/container.xml is missing')
  const opfPath = xmlTags(utf8(container), 'rootfile').find((r) => r['full-path'])?.['full-path']
  if (!opfPath) throw bad('container.xml names no package file')
  const opf = files[resolveHref('', opfPath)]
  if (!opf) throw bad(`its package file ${opfPath} is missing`)

  const opfXml = utf8(opf)
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
    const locked = new Set(xmlTags(utf8(encryption), 'CipherReference').map((c) => resolveHref('', c.URI ?? '')))
    if (chapters.some((c) => locked.has(c))) {
      throw new EpubError(`"${name}" is copy-protected (DRM), so its text cannot be read.`)
    }
  }

  const texts: string[] = []
  for (const path of chapters) {
    const doc = files[path]
    if (!doc) continue
    const text = htmlToText(utf8(doc)).trim()
    if (text) texts.push(text)
  }
  if (texts.length === 0) throw new EpubError(`"${name}" has no text in its chapters.`)
  return texts.join('\n\n')
}
