import { strToU8, zipSync } from 'fflate'
import { describe, expect, it } from 'vitest'

import {
  EpubError,
  MAX_EPUB_ENTRIES,
  MAX_EPUB_UNPACKED_BYTES,
  extractEpubText,
  nodeText,
  resolveHref,
  xmlTags,
  type TextNode,
} from './translateEpub'
import { buildEpub, opfFor, stripTags } from './translateEpubFixture'

const extract = (bytes: Uint8Array) => extractEpubText(bytes, 'book.epub', stripTags)
const errorOf = (bytes: Uint8Array) => {
  try {
    extract(bytes)
  } catch (e) {
    expect(e).toBeInstanceOf(EpubError)
    return (e as Error).message
  }
  throw new Error('expected an EpubError')
}

/** Overwrite the central-directory "uncompressed size" of the entry `name`. */
function declareSize(zip: Uint8Array, name: string, size: number): Uint8Array {
  const out = zip.slice()
  const view = new DataView(out.buffer)
  for (let i = 0; i < out.length - 46; i++) {
    if (view.getUint32(i, true) !== 0x02014b50) continue
    const nameLen = view.getUint16(i + 28, true)
    const entry = new TextDecoder().decode(out.subarray(i + 46, i + 46 + nameLen))
    if (entry === name) {
      view.setUint32(i + 24, size, true)
      return out
    }
  }
  throw new Error(`no entry ${name}`)
}

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

  it('refuses text that would unpack past the cap, before inflating it', () => {
    const epub = buildEpub({ 'OEBPS/a.xhtml': '<p>x</p>' })
    const bomb = declareSize(epub, 'OEBPS/a.xhtml', MAX_EPUB_UNPACKED_BYTES + 1)
    expect(errorOf(bomb)).toBe('"book.epub" unpacks to more than 100 MB of text, too much to open safely.')
  })

  it('images and fonts do not count toward the text cap', () => {
    const epub = buildEpub({ 'OEBPS/a.xhtml': '<p>x</p>' }, { 'OEBPS/big.png': new Uint8Array(8) })
    expect(extract(declareSize(epub, 'OEBPS/big.png', MAX_EPUB_UNPACKED_BYTES + 1))).toBe('x')
  })

  it('an entry that under-declares its size cannot grow past it', () => {
    const body = `<p>${'长'.repeat(5000)}</p>`
    const epub = buildEpub({ 'OEBPS/a.xhtml': body })
    let text = ''
    try {
      text = extract(declareSize(epub, 'OEBPS/a.xhtml', 10))
    } catch (e) {
      expect(e).toBeInstanceOf(EpubError)
    }
    expect(text.length).toBeLessThanOrEqual(10)
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
