import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { SameFixNotice } from './SameFixNotice'

describe('SameFixNotice', () => {
  it('names the fix and how many lines take it, with the lines folded away', () => {
    const offer = { fix: { find: 'Lin', replace: 'Rin' }, matches: [{ id: 3, idx: 2, old_text: 'Lin ran', new_text: 'Rin ran' }] }
    const html = renderToStaticMarkup(
      <SameFixNotice offer={offer} busy={false} onApply={() => {}} onAlways={() => {}} onDismiss={() => {}} />,
    )
    expect(html).toContain('Same fix in 1 other line?')
    expect(html).toContain('<del>Lin</del> → <ins>Rin</ins>')
    expect(html).toContain('Apply and always')
    expect(html).toContain('aria-expanded="false"')
    expect(html).not.toContain('Lin ran')
  })
})
