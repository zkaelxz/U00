import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import type { RunForm } from '../translateForm'
import { GlossaryRetranslateAction } from './GlossaryRetranslate'

const props = { f: {} as RunForm, busy: false, onStarted: () => undefined }
const LABEL = 'Re-translate lines affected by the glossary…'

// The Glossary tab and the Translate tab both mount this one component, so the label, the closed
// state and the list (opened on click) can't drift apart.
describe('GlossaryRetranslateAction', () => {
  it('renders the same button on the Glossary tab and in the Translate action row', () => {
    const tab = renderToStaticMarkup(<GlossaryRetranslateAction {...props} hint="Uses the settings on the Translate tab." />)
    const row = renderToStaticMarkup(<GlossaryRetranslateAction {...props} inline />)
    for (const html of [tab, row]) {
      expect(html).toContain(LABEL)
      expect(html).toContain('glossary-retranslate-open')
      expect(html).not.toContain('glossary-retranslate"')
    }
    expect(tab).toContain('Uses the settings on the Translate tab.')
    expect(row).not.toContain('class="actions"')
  })
})
