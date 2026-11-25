import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { sendLineFeedback } from '../../api/review'
import type { ReviewLine } from '../../types/review'
import { LineFeedback } from './LineFeedback'

const line = { id: 7, en: 'Hello', mark: 'wrong' } as ReviewLine

describe('LineFeedback', () => {
  it('starts as one small button', () => {
    const html = renderToStaticMarkup(<LineFeedback id={3} line={line} onSaved={vi.fn()} />)
    expect(html).toContain('Suggest better wording')
    expect(html).not.toContain('<textarea')
  })

  it('posts the wording with the English that was read', async () => {
    const calls: { url: string; body: unknown }[] = []
    const f = (async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) })
      return new Response(JSON.stringify(line), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }) as unknown as typeof fetch
    await sendLineFeedback(3, 7, '  Hi there ', 'Hello', f)
    expect(calls[0].url).toMatch(/\/dramas\/3\/lines\/7\/feedback$/)
    expect(calls[0].body).toEqual({ better: 'Hi there', expected_en: 'Hello' })
  })
})
