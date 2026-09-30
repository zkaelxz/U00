import { strToU8, zipSync } from 'fflate'
import { describe, expect, it } from 'vitest'

import {
  EpubError,
  MAX_EPUB_ENTRIES,
  MAX_EPUB_UNPACKED_BYTES,
  decodeXml,
  extractEpubText,
  nodeText,
  resolveHref,
  xmlTags,
  type TextNode,
} from './translateEpub'
import { buildEpub, opfFor, stripTags } from './translateEpubFixture'

const extract = (bytes: Uint8Array) => extractEpubText(bytes, 'book.epub', stripTags)
const errorOf = (bytes: Uint8Array, run: (b: Uint8Array) => unknown = extract) => {
  try {
    run(bytes)
  } catch (e) {
    expect(e).toBeInstanceOf(EpubError)
    return (e as Error).message
  }
  throw new Error('expected an EpubError')
}

/** Overwrite a 32-bit field (offset within the record) of `name`'s central-directory record. */
function patchEntry(zip: Uint8Array, name: string, field: number, value: number): Uint8Array {
  const out = zip.slice()
  const view = new DataView(out.buffer)
  for (let i = 0; i < out.length - 46; i++) {
    if (view.getUint32(i, true) !== 0x02014b50) continue
    const nameLen = view.getUint16(i + 28, true)
    const entry = new TextDecoder().decode(out.subarray(i + 46, i + 46 + nameLen))
    if (entry === name) {
      view.setUint32(i + field, value, true)
      return out
    }
  }
  throw new Error(`no entry ${name}`)
}

/**
 * A stored (uncompressed) zip written in the zip64 layout: every size and offset
 * in the central directory is 0xFFFFFFFF with the real value in the zip64 extra
 * field, and the end record points at a zip64 end record. fflate's zipSync
 * never writes zip64, so this is built by hand.
 */
function zip64Stored(files: Record<string, string>): Uint8Array {
  const parts: Uint8Array[] = []
  const central: Uint8Array[] = []
  let at = 0
  for (const [name, text] of Object.entries(files)) {
    const n = strToU8(name)
    const data = strToU8(text)
    const local = new DataView(new ArrayBuffer(30))
    local.setUint32(0, 0x04034b50, true)
    local.setUint32(18, data.length, true)
    local.setUint32(22, data.length, true)
    local.setUint16(26, n.length, true)
    parts.push(new Uint8Array(local.buffer), n, data)
    const rec = new DataView(new ArrayBuffer(46 + n.length + 28))
    rec.setUint32(0, 0x02014b50, true)
    rec.setUint16(8, 0x800, true)
    rec.setUint32(20, 0xffffffff, true)
    rec.setUint32(24, 0xffffffff, true)
    rec.setUint16(28, n.length, true)
    rec.setUint16(30, 28, true)
    rec.setUint32(42, 0xffffffff, true)
    new Uint8Array(rec.buffer).set(n, 46)
    const x = 46 + n.length
    rec.setUint16(x, 1, true)
    rec.setUint16(x + 2, 24, true)
    rec.setBigUint64(x + 4, BigInt(data.length), true)
    rec.setBigUint64(x + 12, BigInt(data.length), true)
    rec.setBigUint64(x + 20, BigInt(at), true)
    central.push(new Uint8Array(rec.buffer))
    at += 30 + n.length + data.length
  }
  const cdSize = central.reduce((sum, c) => sum + c.length, 0)
  const tail = new DataView(new ArrayBuffer(56 + 20 + 22))
  const count = BigInt(central.length)
  tail.setUint32(0, 0x06064b50, true)
  tail.setBigUint64(4, 44n, true)
  tail.setBigUint64(24, count, true)
  tail.setBigUint64(32, count, true)
  tail.setBigUint64(40, BigInt(cdSize), true)
  tail.setBigUint64(48, BigInt(at), true)
  tail.setUint32(56, 0x07064b50, true)
  tail.setBigUint64(64, BigInt(at + cdSize), true)
  tail.setUint32(72, 1, true)
  tail.setUint32(76, 0x06054b50, true)
  tail.setUint16(84, 0xffff, true)
  tail.setUint16(86, 0xffff, true)
  tail.setUint32(88, 0xffffffff, true)
  tail.setUint32(92, 0xffffffff, true)
  const all = [...parts, ...central, new Uint8Array(tail.buffer)]
  const out = new Uint8Array(all.reduce((sum, c) => sum + c.length, 0))
  all.reduce((off, c) => (out.set(c, off), off + c.length), 0)
  return out
}

/** Overwrite the central-directory "uncompressed size" of the entry `name`. */
const declareSize = (zip: Uint8Array, name: string, size: number) => patchEntry(zip, name, 24, size)

describe('extractEpubText', () => {
  it('reads chapters in spine order, a blank line between chapters', () => {
    const epub = buildEpub(
      {
        'OEBPS/a.xhtml': '<body><p>First A</p><p>Second A</p></body>',
        'OEBPS/b.xhtml': '<body><p>Only B</p></body>',
      },
      {
        'OEBPS/content.opf': opfFor(['a.xhtml', 'b.xhtml']).replace(
          '<itemref idref="c0"/>\n<itemref idref="c1"/>',
          '<itemref idref="c1"/>\n<itemref idref="c0"/>',
        ),
      },
    )
    expect(extract(epub)).toBe('Only B\n\nFirst A\nSecond A')
  })

  it('resolves encoded and relative hrefs, skips empty, missing and non-HTML items', () => {
    const opf = `<package><manifest>
      <item id="x" href="Text/Chapter%201.xhtml" media-type="application/xhtml+xml"/>
      <item id="y" href="./Text/../Text/two.xhtml" media-type="application/xhtml+xml"/>
      <item id="e" href="empty.xhtml" media-type="application/xhtml+xml"/>
      <item id="gone" href="gone.xhtml" media-type="application/xhtml+xml"/>
      <item id="img" href="cover.xml" media-type="image/svg+xml"/>
    </manifest><spine><itemref idref="img"/><itemref idref="x"/><itemref idref="e"/>
      <itemref idref="gone"/><itemref idref="y"/><itemref idref="x"/></spine></package>`
    const epub = buildEpub(
      {},
      {
        'OEBPS/content.opf': opf,
        'OEBPS/Text/Chapter 1.xhtml': '<p>One</p>',
        'OEBPS/Text/two.xhtml': '<p>Two</p><script>alert(1)</script>',
        'OEBPS/empty.xhtml': '<p> </p>',
        'OEBPS/cover.xml': '<svg><text>cover</text></svg>',
      },
    )
    expect(extract(epub)).toBe('One\n\nTwo')
  })

  it('refuses a file that is not a zip', () => {
    expect(errorOf(strToU8('hello'))).toBe('"book.epub" is not a readable EPUB (it could not be unzipped).')
  })

  it('refuses a zip with no container.xml, no rootfile or no OPF', () => {
    expect(errorOf(buildEpub({ 'OEBPS/a.xhtml': '<p>x</p>' }, { 'META-INF/container.xml': undefined }))).toMatch(
      /META-INF\/container.xml is missing/,
    )
    expect(errorOf(buildEpub({}, { 'META-INF/container.xml': '<container></container>' }))).toMatch(
      /names no package file/,
    )
    expect(errorOf(buildEpub({}, { 'OEBPS/content.opf': undefined }))).toMatch(
      /its package file OEBPS\/content.opf is missing/,
    )
  })

  it('says when there are no chapters, or no text in them', () => {
    expect(errorOf(buildEpub({}))).toBe('"book.epub" has no chapters to read.')
    expect(errorOf(buildEpub({ 'OEBPS/a.xhtml': '<p></p>' }))).toBe('"book.epub" has no text in its chapters.')
  })

  it('refuses a DRM-protected book but not one with only obfuscated fonts', () => {
    const enc = (uri: string) =>
      `<encryption><enc:EncryptedData><enc:CipherData><enc:CipherReference URI="${uri}"/></enc:CipherData></enc:EncryptedData></encryption>`
    const chapters = { 'OEBPS/a.xhtml': '<p>x</p>' }
    expect(errorOf(buildEpub(chapters, { 'META-INF/encryption.xml': enc('OEBPS/a.xhtml') }))).toMatch(/DRM/)
    expect(extract(buildEpub(chapters, { 'META-INF/encryption.xml': enc('OEBPS/font.otf') }))).toBe('x')
  })

  it(`refuses more than ${MAX_EPUB_ENTRIES} entries`, () => {
    const files: Record<string, Uint8Array> = {}
    for (let i = 0; i <= MAX_EPUB_ENTRIES; i++) files[`i/${i}.png`] = new Uint8Array(0)
    expect(errorOf(zipSync(files))).toBe('"book.epub" has more than 10,000 files inside, too many to open safely.')
  })

  it('refuses a real zip bomb at the default cap, whatever size it declares', () => {
    // ~100 KB of deflated zeros that really unpack to just over 100 MB.
    const zeros = new Uint8Array(MAX_EPUB_UNPACKED_BYTES + 1)
    const bomb = declareSize(buildEpub({}, { 'OEBPS/a.xhtml': zeros }), 'OEBPS/a.xhtml', 0)
    expect(bomb.length).toBeLessThan(200_000)
    expect(errorOf(bomb)).toBe('"book.epub" unpacks to more than 100 MB of text, too much to open safely.')
  })

  it('ignores declared sizes: a small chapter declaring a huge size still opens', () => {
    const epub = buildEpub({ 'OEBPS/a.xhtml': '<p>x</p>' })
    expect(extract(declareSize(epub, 'OEBPS/a.xhtml', MAX_EPUB_UNPACKED_BYTES + 1))).toBe('x')
  })

  // A 4 KB cap keeps these fixtures tiny; the default cap is 100 MB.
  const smallCap = (b: Uint8Array) =>
    extractEpubText(b, 'book.epub', stripTags, { maxEntries: 100, maxUnpackedBytes: 4096 })

  it('a stored entry counts its stored bytes, even when it declares size 0', () => {
    const stored = buildEpub({ 'OEBPS/a.xhtml': `<p>${'x'.repeat(5000)}</p>` }, {}, { level: 0 })
    expect(errorOf(stored, smallCap)).toMatch(/too much to open safely/)
    expect(errorOf(declareSize(stored, 'OEBPS/a.xhtml', 0), smallCap)).toMatch(/too much to open safely/)
    expect(smallCap(buildEpub({ 'OEBPS/a.xhtml': '<p>ok</p>' }, {}, { level: 0 }))).toBe('ok')
  })

  it('a small, highly compressible entry declaring size 0 is refused on its real size', () => {
    const zeros = new Uint8Array(1024 * 1024)
    const bomb = declareSize(buildEpub({}, { 'OEBPS/a.xhtml': zeros }), 'OEBPS/a.xhtml', 0)
    expect(bomb.length).toBeLessThan(4096)
    expect(errorOf(bomb, smallCap)).toMatch(/too much to open safely/)
  })

  it('a deflated entry declaring size 0 still counts its compressed bytes', () => {
    // Varied CJK text barely compresses, so the stored bytes alone pass 4 KB.
    const varied = Array.from({ length: 6000 }, (_, i) => String.fromCharCode(0x4e00 + ((i * 7919) % 20000))).join('')
    const epub = buildEpub({ 'OEBPS/a.xhtml': `<p>${varied}</p>` })
    expect(errorOf(declareSize(epub, 'OEBPS/a.xhtml', 0), smallCap)).toMatch(/too much to open safely/)
  })

  it('images and fonts do not count toward the text cap', () => {
    const epub = buildEpub({ 'OEBPS/a.xhtml': '<p>x</p>' }, { 'OEBPS/big.png': new Uint8Array(8) })
    expect(extract(declareSize(epub, 'OEBPS/big.png', MAX_EPUB_UNPACKED_BYTES + 1))).toBe('x')
  })

  it('an entry that under-declares its size is still read in full', () => {
    const epub = buildEpub({ 'OEBPS/a.xhtml': `<p>${'长'.repeat(5000)}</p>` })
    expect(extract(declareSize(epub, 'OEBPS/a.xhtml', 10))).toBe('长'.repeat(5000))
  })

  it('reads a zip64-layout book', () => {
    const epub = zip64Stored({
      'META-INF/container.xml': '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
      'OEBPS/content.opf': opfFor(['a.xhtml']),
      'OEBPS/a.xhtml': '<p>Zip64 chapter</p>',
    })
    expect(extract(epub)).toBe('Zip64 chapter')
  })

  it('refuses a zip whose directory points past the end of the file', () => {
    const epub = buildEpub({ 'OEBPS/a.xhtml': '<p>x</p>' })
    expect(errorOf(epub.subarray(0, 40))).toMatch(/could not be unzipped/)
    expect(errorOf(patchEntry(epub, 'OEBPS/a.xhtml', 20, epub.length))).toMatch(/could not be unzipped/)
    expect(errorOf(patchEntry(epub, 'OEBPS/a.xhtml', 42, epub.length))).toMatch(/could not be unzipped/)
  })
})

describe('nodeText', () => {
  const t = (value: string): TextNode => ({ nodeType: 3, nodeName: '#text', nodeValue: value, childNodes: [] })
  const el = (name: string, ...children: TextNode[]): TextNode => ({
    nodeType: 1,
    nodeName: name,
    nodeValue: null,
    childNodes: children,
  })

  it('one line per block, inline text joined, whitespace collapsed', () => {
    const body = el(
      'BODY',
      el('H1', t('  Chapter\n 1 ')),
      el('P', t('Hello '), el('SPAN', t('there')), t('.')),
      el('DIV', el('P', t('A')), t('line'), el('BR'), t('break')),
      t('\n   \n'),
    )
    expect(nodeText(body)).toBe('Chapter 1\nHello there.\nA\nline\nbreak')
  })

  it('skips script, style, head and non-element nodes, and handles prefixed names', () => {
    const comment: TextNode = { nodeType: 8, nodeName: '#comment', nodeValue: 'no', childNodes: [] }
    const doc = el(
      'html',
      el('head', el('title', t('Title'))),
      el('body', el('script', t('alert(1)')), el('style', t('p{}')), comment, el('xhtml:p', t('Kept'))),
    )
    expect(nodeText(doc)).toBe('Kept')
  })
})

describe('decodeXml', () => {
  it('ignores a UTF-16 label on a declaration it could read as ASCII', () => {
    expect(decodeXml(strToU8('<?xml version="1.0" encoding="UTF-16"?><p>你好</p>'))).toContain('<p>你好</p>')
  })

  it('honours a declared encoding and defaults to UTF-8', () => {
    const decl = strToU8('<?xml version="1.0" encoding="GBK"?><p>')
    const gbk = new Uint8Array([...decl, 0xc4, 0xe3, 0xba, 0xc3, ...strToU8('</p>')])
    expect(decodeXml(gbk)).toBe('<?xml version="1.0" encoding="GBK"?><p>你好</p>')
    expect(decodeXml(strToU8('<p>你好</p>'))).toBe('<p>你好</p>')
    expect(decodeXml(strToU8('<?xml version="1.0" encoding="no-such"?><p>é</p>'))).toContain('<p>é</p>')
  })
})

describe('xmlTags / resolveHref', () => {
  it('reads attributes with prefixes, either quote and entities, ignoring comments', () => {
    const xml = `<!-- <item id="fake" href="f"/> --><opf:item id='a' href="x&amp;y%20z.xhtml" opf:media-type="t"/><itemref idref="a"/>`
    expect(xmlTags(xml, 'item')).toEqual([{ id: 'a', href: 'x&y%20z.xhtml', 'media-type': 't' }])
  })
  it('resolves against the OPF folder and normalizes dots', () => {
    expect(resolveHref('OEBPS', 'Text/a%20b.xhtml#frag')).toBe('OEBPS/Text/a b.xhtml')
    expect(resolveHref('OEBPS/x', '../y/./c.xhtml')).toBe('OEBPS/y/c.xhtml')
    expect(resolveHref('', 'content.opf')).toBe('content.opf')
  })
})
