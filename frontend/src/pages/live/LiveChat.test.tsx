import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { LiveChat } from './LiveChat'

const msg = (id: number, state: string, extra = {}) => ({ id, author: `A${id}`, text: `t${id}`, paid: '', translation: state === 'done' ? `e${id}` : '', state, ...extra })

describe('LiveChat', () => {
  it('shows the original with its translation, newest first', () => {
    const html = renderToStaticMarkup(<LiveChat language="zh" view={{ state: 'reading', message: 'Reading the chat.', messages: [msg(1, 'done'), msg(2, 'done')] }} />)
    expect(html.indexOf('t2')).toBeLessThan(html.indexOf('t1'))
    expect(html).toContain('e1')
    expect(html).toContain('lang="zh"')
  })

  it('shows a message with nothing to translate as written, and says when one is waiting or failed', () => {
    const html = renderToStaticMarkup(<LiveChat language="ja" view={{ state: 'reading', message: '', messages: [msg(1, 'skipped'), msg(2, 'pending'), msg(3, 'failed')] }} />)
    expect(html).toContain('t1')
    expect(html).toContain('Translating…')
    expect(html).toContain('Not translated.')
  })

  it('shows a Super Chat amount and the chat status', () => {
    const html = renderToStaticMarkup(<LiveChat language="zh" view={{ state: 'unavailable', message: 'This stream has no live chat to read.', messages: [msg(1, 'done', { paid: '$5.00' })] }} />)
    expect(html).toContain('$5.00')
    expect(html).toContain('This stream has no live chat to read.')
  })

  it('says nothing has arrived yet while reading', () => {
    expect(renderToStaticMarkup(<LiveChat language="zh" view={{ state: 'reading', message: '', messages: [] }} />)).toContain('No chat messages yet.')
  })
})
