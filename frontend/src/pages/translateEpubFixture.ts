/*
 * Test helpers: build a tiny EPUB in memory (fflate zipSync) and a DOM-free
 * stand-in for DOMParser, since vitest runs in node. Used by the vitest files
 * only; the real DOMParser path is covered by e2e/translate.spec.ts.
 */
import { strToU8, zipSync, type ZipOptions, type Zippable } from 'fflate'

const CONTAINER = `<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>`

/** An OPF whose spine lists `chapters` (paths relative to OEBPS/) in order. */
export function opfFor(chapters: string[]): string {
  const items = chapters
    .map((c, i) => `<item id="c${i}" href="${c}" media-type="application/xhtml+xml"/>`)
    .join('\n')
  const refs = chapters.map((_c, i) => `<itemref idref="c${i}"/>`).join('\n')
  return `<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0">
  <manifest>${items}<item id="css" href="s.css" media-type="text/css"/></manifest>
  <spine>${refs}</spine>
</package>`
}

/**
 * Zip the given chapter files (keys are full zip paths under OEBPS/) with a
 * container.xml and an OPF listing them in key order; `extra` adds or
 * overrides raw entries (pass undefined to drop one). `options` goes to
 * zipSync ({ level: 0 } stores every entry uncompressed).
 */
export function buildEpub(
  chapters: Record<string, string>,
  extra: Record<string, string | Uint8Array | undefined> = {},
  options: ZipOptions = {},
): Uint8Array {
  const files: Record<string, string | Uint8Array | undefined> = {
    mimetype: 'application/epub+zip',
    'META-INF/container.xml': CONTAINER,
    'OEBPS/content.opf': opfFor(Object.keys(chapters).map((k) => k.replace(/^OEBPS\//, ''))),
    ...chapters,
    ...extra,
  }
  const zippable: Zippable = {}
  for (const [k, v] of Object.entries(files)) {
    if (v !== undefined) zippable[k] = typeof v === 'string' ? strToU8(v) : v
  }
  return zipSync(zippable, options)
}

/** Crude markup stripper standing in for DOMParser in node tests. */
export function stripTags(markup: string): string {
  return markup
    .replace(/<(script|style)[\s\S]*?<\/\1>/gi, '')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
    .join('\n')
}
