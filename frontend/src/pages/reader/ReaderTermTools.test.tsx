import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { ReplaceEverywhere, TermEditor } from './ReaderTermTools'

const noop = () => undefined

describe('Reader term tools', () => {
  it('shows an existing term with Delete and its original fixed', () => {
    const out = renderToStaticMarkup(
      <TermEditor dramaId={1} draft={{ id: 3, term_original: '姑姑', term_translation: 'Auntie', category: 'honorific', notes: 'Gugu' }}
        onClose={noop} onChanged={noop} />,
    )
    expect(out).toContain('姑姑')
    expect(out).toContain('Gugu')
    expect(out).toContain('>Delete term<')
    expect(out).not.toContain('Original')
  })

  it('asks for the original when a term is made from a selection, with no Delete', () => {
    const out = renderToStaticMarkup(
      <TermEditor dramaId={1} draft={{ term_original: '', term_translation: 'Auntie', category: 'other', notes: '' }}
        onClose={noop} onChanged={noop} />,
    )
    expect(out).toContain('Original')
    expect(out).not.toContain('>Delete term<')
  })

  it('names the selected text in the replace panel', () => {
    const out = renderToStaticMarkup(
      <ReplaceEverywhere dramaId={1} selected="Gugu" onClose={noop} onApplied={noop} onAddTerm={noop} />,
    )
    expect(out).toContain('Gugu')
    expect(out).toContain('Add as term')
  })
})
