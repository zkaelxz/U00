import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { GroupedPicker } from './GroupedPicker'

const groups = [
  { label: 'Styles', options: [{ value: 'a', label: 'Alpha' }, { value: 'b', label: 'Beta' }] },
  { label: 'Tiers', options: [{ value: 'tier:x', label: 'Gamma' }] },
]

describe('GroupedPicker', () => {
  it('shows the chosen option on a button, not a native select', () => {
    const html = renderToStaticMarkup(<GroupedPicker title="Style" value="b" groups={groups} onPick={() => undefined} />)
    expect(html).not.toContain('<select')
    expect(html).toContain('class="picker-current">Beta<')
    expect(html).toContain('aria-haspopup="dialog"')
  })

  it('passes the Field ids to the button and honours disabled', () => {
    const html = renderToStaticMarkup(<GroupedPicker title="Style" value="a" groups={groups} disabled id="f1" aria-describedby="h1" onPick={() => undefined} />)
    expect(html).toContain('id="f1"')
    expect(html).toContain('aria-describedby="h1"')
    expect(html).toContain('disabled=""')
  })
})
